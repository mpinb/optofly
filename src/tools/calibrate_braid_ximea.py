"""Interactive camera FOV calibration — one or two z-planes.

Requires: Ximea camera, Braid running, liquid lens connected.  No ZMQ stack needed.

Usage:
    uv run python -m src.tools.calibrate_braid_ximea
    uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml
    uv run python -m src.tools.calibrate_braid_ximea --no-quiet  # show SSE retry messages

Workflow:
    1. Move a bright target (laser/LED) to each edge of the camera frame at your
       desired height.  Press SPACE each time to record the current Braid (x, y, z).
       A cyan circle shows the auto-detected spot; left-click as a fallback.
       Collect >= 4 boundary points (at least one near each edge).

    2. Press 'n' when done.  The tool derives the plane z from the median of the
       recorded Braid z values and refocuses the lens to that height automatically.

    3. Choose:
         's' — save as flat [camera.FOV] and quit.
         'a' — add a second plane (move target to the other height and repeat 1-2).

    4. After the second plane, press 's' to save as [camera.FOV.near] +
       [camera.FOV.far].  The lower-z plane is always "near" and higher-z is "far".

    Other keys: u=undo last point  q=quit without saving

    Save prompts default to Yes (press Enter to accept, 'n' to decline).
"""

import argparse
import ctypes
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from optotune_lens import ICC1C as LensDriver
from src.processes.lens import setup_lens_calibration
from src.utils.config import BraidPublisherConfig, LiquidLensConfig

_DATA_PREFIX = "data: "
_MAX_RETRIES = 5
_RETRY_DELAY = 2
_MIN_POINTS = 4


def _parse_chunk(chunk: str) -> dict:
    lines = chunk.strip().split("\n")
    if len(lines) != 2:
        raise ValueError(f"Expected 2 lines, got {len(lines)}")
    if lines[0] != "event: braid":
        raise ValueError(f"Unexpected event line: {lines[0]!r}")
    if not lines[1].startswith(_DATA_PREFIX):
        raise ValueError(f"Unexpected data line: {lines[1]!r}")
    return json.loads(lines[1][len(_DATA_PREFIX) :])


parse_chunk = _parse_chunk  # public alias for other modules that import this


# ---------------------------------------------------------------------------
# BRAID tracker thread (SSE — connects directly to Braid, no ZMQ required)
# ---------------------------------------------------------------------------


class _BraidTracker(threading.Thread):
    def __init__(
        self, braid_url: str, stop_event: threading.Event, quiet: bool = False
    ) -> None:
        super().__init__(daemon=True, name="braid-tracker")
        self._url = braid_url.rstrip("/")
        self._stop_event = stop_event
        self._quiet = quiet
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._pos: tuple[float, float, float] | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                self._session.get(self._url, timeout=2).raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_DELAY)
                else:
                    raise RuntimeError(
                        f"Cannot reach Braid at {self._url} after {_MAX_RETRIES} attempts: {e}"
                    )

    @property
    def position(self) -> tuple[float, float, float] | None:
        with self._lock:
            return self._pos

    def run(self) -> None:
        events_url = f"{self._url}/events"
        while not self._stop_event.is_set():
            try:
                resp = self._session.get(
                    events_url,
                    stream=True,
                    headers={"Accept": "text/event-stream"},
                    timeout=10,
                )
                resp.raise_for_status()
                for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                    if self._stop_event.is_set():
                        break
                    try:
                        data = _parse_chunk(chunk)
                        msg = data.get("msg", {})
                        update = msg.get("Update") or msg.get("Birth")
                        if update:
                            with self._lock:
                                self._pos = (
                                    float(update["x"]),
                                    float(update["y"]),
                                    float(update["z"]),
                                )
                    except Exception:
                        pass
            except requests.RequestException as e:
                if not self._stop_event.is_set():
                    if not self._quiet:
                        print(f"  Braid connection lost: {e} — retrying in 1 s")
                    time.sleep(1)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------


def _open_ximea_camera(
    width: int = 2112,
    height: int = 2112,
    fps: float = 10.0,
    exposure_us: int = 2000,
) -> tuple:
    from ximea import Camera, Image

    cam = Camera()
    cam.open_device()
    cam.set_imgdataformat("XI_MONO8")
    cam.set_exposure(exposure_us)
    cam.enable_bpc()
    cam.set_column_fpn_correction("XI_ON")

    cam.set_width(width)
    cam.set_height(height)
    sensor_w = cam.get_width_maximum()
    sensor_h = cam.get_height_maximum()
    offset_x = (sensor_w - width) // 2
    offset_y = (sensor_h - height) // 2
    inc_x = cam.get_offsetX_increment()
    inc_y = cam.get_offsetY_increment()
    cam.set_offsetX((offset_x // inc_x) * inc_x)
    cam.set_offsetY((offset_y // inc_y) * inc_y)
    cam.set_acq_timing_mode("XI_ACQ_TIMING_MODE_FRAME_RATE_LIMIT")
    cam.set_framerate(fps)

    actual_w = cam.get_width()
    actual_h = cam.get_height()
    cam.start_acquisition()
    return cam, Image(), actual_w, actual_h


# ---------------------------------------------------------------------------
# Bright-spot detection
# ---------------------------------------------------------------------------


def _detect_bright_spot(
    gray: np.ndarray,
    threshold: int = 200,
    min_area: int = 5,
) -> tuple[float, float] | None:
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    n_labels, _, stats, centroids = cv2.connectedComponentsWithStats(
        mask, connectivity=8
    )
    best_label = -1
    best_area = min_area - 1
    for label in range(1, n_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area > best_area:
            best_area = area
            best_label = label
    if best_label == -1:
        return None
    cx, cy = centroids[best_label]
    return float(cx), float(cy)


# ---------------------------------------------------------------------------
# FOV helpers
# ---------------------------------------------------------------------------


def _compute_fov(points_xy: list[tuple[float, float]]) -> dict[str, float]:
    xs = [p[0] for p in points_xy]
    ys = [p[1] for p in points_xy]
    return {
        "x_min": float(min(xs)),
        "x_max": float(max(xs)),
        "y_min": float(min(ys)),
        "y_max": float(max(ys)),
    }


def _print_fov(label: str, z: float, fov: dict[str, float]) -> None:
    print(f"\n  {label}  z = {z:.4f} m:")
    print(
        f"    x [{fov['x_min']:.5f}, {fov['x_max']:.5f}]"
        f"  ({(fov['x_max'] - fov['x_min']) * 1000:.1f} mm wide)"
    )
    print(
        f"    y [{fov['y_min']:.5f}, {fov['y_max']:.5f}]"
        f"  ({(fov['y_max'] - fov['y_min']) * 1000:.1f} mm tall)"
    )


# ---------------------------------------------------------------------------
# Config writers
# ---------------------------------------------------------------------------

_CALIBRATION_STAMP_RE = re.compile(r"^# Calibrated on .*\n", re.MULTILINE)


def _stamp_calibration_comment(text: str, header_pattern: str) -> str:
    """Insert (or refresh) a '# Calibrated on <timestamp>' comment right above
    the line matching header_pattern."""
    lines = text.splitlines(keepends=True)
    header_idx = next(
        (i for i, line in enumerate(lines) if re.match(header_pattern, line)), None
    )
    if header_idx is None:
        return text
    if header_idx > 0 and _CALIBRATION_STAMP_RE.match(lines[header_idx - 1]):
        del lines[header_idx - 1]
        header_idx -= 1
    stamp = f"# Calibrated on {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    lines.insert(header_idx, stamp)
    return "".join(lines)


def _write_fov_to_config(config_path: str, fov: dict[str, float]) -> None:
    text = Path(config_path).read_text()
    if not re.search(r"^\[camera\.FOV\]", text, re.MULTILINE):
        raise RuntimeError(
            f"[camera.FOV] section not found in {config_path}. "
            "Add it manually first (see configs/config.example.toml)."
        )
    for key, val in fov.items():
        pattern = rf"^({re.escape(key)}\s*=\s*)[-\d.e+]+"
        replacement = rf"\g<1>{val:.5f}"
        new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        if n == 0:
            raise RuntimeError(
                f"Key '{key}' not found under [camera.FOV] in {config_path}"
            )
        text = new_text
    text = _stamp_calibration_comment(text, r"^\[camera\.FOV\]\s*$")
    Path(config_path).write_text(text)


def _write_frustum_to_config(
    config_path: str,
    near_z: float,
    near: dict[str, float],
    far_z: float,
    far: dict[str, float],
) -> None:
    text = Path(config_path).read_text()
    lines = text.splitlines(keepends=True)

    fov_start = None
    for i, line in enumerate(lines):
        if re.match(r"^\[camera\.FOV\]\s*$", line):
            fov_start = i
            break
    if fov_start is None:
        raise RuntimeError(
            f"[camera.FOV] section not found in {config_path}. "
            "Add it manually first (see configs/config.example.toml)."
        )
    if fov_start > 0 and _CALIBRATION_STAMP_RE.match(lines[fov_start - 1]):
        fov_start -= 1

    fov_end = len(lines)
    for i in range(fov_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and not re.match(r"^\[camera\.FOV", stripped):
            fov_end = i
            break

    new_block = (
        f"# Calibrated on {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        "[camera.FOV]\n"
        "# Frustum mode — generated by calibrate_braid_ximea.py\n"
        "# To revert to flat mode, replace this block with flat x_min/x_max/y_min/y_max keys.\n"
        "\n"
        "[camera.FOV.near]\n"
        f"z     = {near_z:.4f}      # z where these bounds were measured\n"
        f"x_min = {near['x_min']:.5f}\n"
        f"x_max = {near['x_max']:.5f}\n"
        f"y_min = {near['y_min']:.5f}\n"
        f"y_max = {near['y_max']:.5f}\n"
        "\n"
        "[camera.FOV.far]\n"
        f"z     = {far_z:.4f}      # z where these bounds were measured\n"
        f"x_min = {far['x_min']:.5f}\n"
        f"x_max = {far['x_max']:.5f}\n"
        f"y_min = {far['y_min']:.5f}\n"
        f"y_max = {far['y_max']:.5f}\n"
        "\n"
    )

    new_lines = lines[:fov_start] + [new_block] + lines[fov_end:]
    Path(config_path).write_text("".join(new_lines))


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

_GREEN = (0, 220, 0)
_YELLOW = (0, 200, 220)
_WHITE = (240, 240, 240)
_CYAN = (220, 200, 0)
_ORANGE = (0, 140, 255)
_GREY = (120, 120, 120)
_FONT = cv2.FONT_HERSHEY_SIMPLEX

_PHASE_COLORS = [_GREEN, _ORANGE]
_PHASE_LABELS = ["Plane 1", "Plane 2"]


def _draw_overlay(
    frame: np.ndarray,
    phase: int,
    phase_done: list[bool],
    points: list[list[tuple[float, float]]],
    plane_z: list[float | None],
    braid_pos: tuple[float, float, float] | None,
    detected_spot: tuple[float, float] | None,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]

    # Draw points for each phase
    for ph in range(2):
        color = _PHASE_COLORS[ph] if ph == phase else _GREY
        for i, (px, py) in enumerate(points[ph]):
            u, v = int(round(px)), int(round(py))
            cv2.drawMarker(vis, (u, v), color, cv2.MARKER_CROSS, 22, 3)
            cv2.putText(
                vis, f"{i + 1}", (u + 12, v - 10), _FONT, 0.65, color, 2, cv2.LINE_AA
            )

    # Live detected spot
    if detected_spot is not None:
        u, v = int(round(detected_spot[0])), int(round(detected_spot[1]))
        cv2.circle(vis, (u, v), 16, _CYAN, 3)
        cv2.drawMarker(vis, (u, v), _CYAN, cv2.MARKER_CROSS, 30, 2)

    # Status panel
    current_pts = points[phase]
    lines: list[tuple[str, tuple]] = []

    if braid_pos:
        lines.append(
            (
                f"Braid: ({braid_pos[0]:.3f}, {braid_pos[1]:.3f}, {braid_pos[2]:.3f})",
                _WHITE,
            )
        )
    else:
        lines.append(("Braid: no fix", _WHITE))

    for ph in range(2):
        if not points[ph] and ph > phase:
            break
        z_str = f"  z={plane_z[ph]:.4f} m" if plane_z[ph] is not None else ""
        pts_str = f"{len(points[ph])} pts"
        done_str = " (done)" if phase_done[ph] else ""
        color = _PHASE_COLORS[ph] if ph == phase else _GREY
        lines.append((f"{_PHASE_LABELS[ph]}: {pts_str}{z_str}{done_str}", color))

    if current_pts and not phase_done[phase]:
        xs = [p[0] for p in current_pts]
        ys = [p[1] for p in current_pts]
        lines.append(
            (
                f"  x [{min(xs):.4f}, {max(xs):.4f}]"
                f"  ({(max(xs) - min(xs)) * 1000:.1f} mm)",
                _CYAN,
            )
        )
        lines.append(
            (
                f"  y [{min(ys):.4f}, {max(ys):.4f}]"
                f"  ({(max(ys) - min(ys)) * 1000:.1f} mm)",
                _CYAN,
            )
        )

    line_height = 32
    panel_h = 20 + len(lines) * line_height
    overlay = vis.copy()
    cv2.rectangle(overlay, (0, 0), (w, panel_h), (0, 0, 0), -1)
    vis = cv2.addWeighted(overlay, 0.45, vis, 0.55, 0)

    for i, (line, color) in enumerate(lines):
        cv2.putText(
            vis, line, (14, 32 + i * line_height), _FONT, 0.75, color, 2, cv2.LINE_AA
        )

    # Help text
    if phase_done[0] and not phase_done[1]:
        help_text = "[s] save flat FOV   [a] add second plane   [u] undo   [q] quit"
    elif phase_done[1]:
        help_text = "[s] save frustum FOV   [u] undo   [q] quit"
    else:
        need = max(0, _MIN_POINTS - len(current_pts))
        need_str = f"need {need} more point(s)   " if need > 0 else ""
        help_text = (
            f"SPACE or CLICK: add point   {need_str}[n] finalize plane"
            "   [u] undo   [q] quit"
        )

    help_bar_h = 46
    overlay = vis.copy()
    cv2.rectangle(overlay, (0, h - help_bar_h), (w, h), (0, 0, 0), -1)
    vis = cv2.addWeighted(overlay, 0.45, vis, 0.55, 0)
    cv2.putText(vis, help_text, (14, h - 16), _FONT, 0.70, _YELLOW, 2, cv2.LINE_AA)
    return vis


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Camera FOV calibration — one or two z-planes, z auto-read from Braid"
    )
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--exposure", type=int, default=2000)
    parser.add_argument(
        "--threshold",
        type=int,
        default=200,
        help="Brightness threshold for auto-detect (0-255)",
    )
    parser.add_argument(
        "--quiet",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Suppress 'Braid connection lost' retry messages (e.g. from idle "
        "SSE gaps with no tracked object) so they don't obscure calibration "
        "instructions. On by default; pass --no-quiet to see retry messages "
        "for debugging Braid connectivity",
    )
    args = parser.parse_args()

    # --- Lens ---
    try:
        lens_cfg = LiquidLensConfig.from_path(args.config)
        lens_cal = setup_lens_calibration(
            lens_cfg.calibration_file, lens_cfg.calibration_model
        )
        lens = LensDriver(lens_cfg.port)
        lens.to_focal_power_mode()
        print(f"  Lens opened on {lens_cfg.port}")
    except Exception as e:
        print(f"ERROR: Cannot open liquid lens: {e}")
        sys.exit(1)

    # --- Braid ---
    braid_cfg = BraidPublisherConfig.from_path(args.config)
    stop_event = threading.Event()
    try:
        tracker = _BraidTracker(braid_cfg.url, stop_event, quiet=args.quiet)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        lens.close()
        sys.exit(1)
    tracker.start()
    print(f"  Connected to Braid SSE at {braid_cfg.url}")

    # --- Camera ---
    try:
        cam, img_obj, frame_w, frame_h = _open_ximea_camera(
            fps=args.fps, exposure_us=args.exposure
        )
        print(f"  Ximea camera: {frame_w}x{frame_h} @ {args.fps} fps\n")
    except Exception as e:
        print(f"ERROR: Cannot open Ximea camera: {e}")
        stop_event.set()
        lens.close()
        sys.exit(1)

    gray_buf = np.empty((frame_h, frame_w), dtype=np.uint8)

    # State: points[0] = plane-1 Braid (x,y) list, points[1] = plane-2
    points: list[list[tuple[float, float]]] = [[], []]
    z_values: list[list[float]] = [[], []]  # Braid z per recorded point, per phase
    plane_z: list[float | None] = [None, None]
    phase_done: list[bool] = [False, False]
    phase = 0

    click_pending: list[tuple[int, int]] = []

    def _on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pending.append((x, y))

    window_name = "Camera FOV Calibration"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    # WINDOW_NORMAL opens at a small default size (unlike WINDOW_AUTOSIZE, it
    # doesn't auto-fit the image) — size it explicitly so it's comfortably
    # large and readable, without exceeding a typical monitor.
    _MAX_WINDOW_DIM = 1400
    _MIN_WINDOW_DIM = 900
    _scale = min(1.0, _MAX_WINDOW_DIM / max(frame_w, frame_h))
    win_w = max(int(frame_w * _scale), _MIN_WINDOW_DIM)
    win_h = max(int(frame_h * _scale), _MIN_WINDOW_DIM)
    cv2.resizeWindow(window_name, win_w, win_h)
    cv2.setMouseCallback(window_name, _on_mouse)

    print(
        "\n"
        "==================== Camera FOV Calibration ====================\n"
        "  STEP 1 — Record boundary points at this height\n"
        "    Move a bright target (laser/LED) to each edge of the camera\n"
        "    frame in turn: LEFT, RIGHT, TOP, BOTTOM. At each edge:\n"
        "      - Press SPACE to auto-detect and record the point, or\n"
        "      - Left-click directly on the target in the video window.\n"
        "    Collect at least 4 points total (one per edge).\n"
        "\n"
        "  STEP 2 — Finalize the plane\n"
        "    Press 'n'. The plane height (z) is read automatically from Braid.\n"
        "\n"
        "  STEP 3 — Save or add a second plane\n"
        "    - Press 's' to save this single height as a flat FOV and quit, or\n"
        "    - Press 'a' to add a second plane at a different height, then\n"
        "      repeat STEP 1-2 there for a frustum (near/far) FOV.\n"
        "    Save prompts default to Yes — press Enter to accept.\n"
        "\n"
        "  Other keys:  'u' undo last point     'q' quit without saving\n"
        "==================================================================\n"
    )

    def _record_point(u: float | None, v: float | None) -> None:
        braid_pos = tracker.position
        if braid_pos is None:
            print("  [skip] No Braid fix — move target into tracking volume")
            return
        x, y, z = braid_pos
        points[phase].append((x, y))
        z_values[phase].append(z)
        src = f"pixel ({int(u)},{int(v)})" if u is not None else "click"
        print(
            f"  Phase {phase + 1} pt {len(points[phase])}: "
            f"Braid ({x:.4f}, {y:.4f}, {z:.4f})  [{src}]"
        )

    def _finalise_plane() -> bool:
        """Compute z from median, set lens, print FOV. Returns True on success."""
        if len(points[phase]) < _MIN_POINTS:
            print(f"  Need >= {_MIN_POINTS} points (have {len(points[phase])})")
            return False
        z_med = float(np.median(z_values[phase]))
        plane_z[phase] = z_med
        dpt = lens_cal.get_dpt(z_med)
        lens.set_diopter(dpt)
        fov = _compute_fov(points[phase])
        _print_fov(f"Plane {phase + 1}", z_med, fov)
        print(f"  Lens refocused to z={z_med:.4f} m ({dpt:+.3f} dpt)")
        return True

    try:
        while True:
            try:
                cam.get_image(img_obj, timeout=2000)
            except Exception as e:
                print(f"Camera read failed: {e}")
                break

            ctypes.memmove(gray_buf.ctypes.data, img_obj.bp, frame_w * frame_h)
            frame = cv2.cvtColor(gray_buf, cv2.COLOR_GRAY2BGR)
            detected_spot = _detect_bright_spot(gray_buf, threshold=args.threshold)

            if click_pending:
                u, v = click_pending.pop(0)
                if not phase_done[phase]:
                    _record_point(float(u), float(v))

            vis = _draw_overlay(
                frame,
                phase,
                phase_done,
                points,
                plane_z,
                tracker.position,
                detected_spot,
            )
            cv2.imshow(window_name, vis)
            key = cv2.waitKey(30) & 0xFF

            if key == ord("q"):
                print("\nQuit without saving.")
                break

            elif key == ord(" "):
                if phase_done[phase]:
                    print(
                        "  This plane is finalised. Press 's' to save or 'a' to add a plane."
                    )
                elif detected_spot is not None:
                    _record_point(detected_spot[0], detected_spot[1])
                else:
                    print(
                        "  [skip] No bright spot detected — adjust --threshold or click manually"
                    )

            elif key == ord("u"):
                if points[phase] and not phase_done[phase]:
                    removed_xy = points[phase].pop()
                    z_values[phase].pop()
                    print(
                        f"  Undid point: Braid ({removed_xy[0]:.4f}, {removed_xy[1]:.4f})"
                    )
                elif phase_done[phase]:
                    print(
                        "  Plane already finalised — cannot undo. Start over with 'q'."
                    )
                else:
                    print("  Nothing to undo")

            elif key == ord("n"):
                if phase_done[phase]:
                    print(
                        "  Already finalised. Press 's' to save or 'a' to add a plane."
                    )
                elif _finalise_plane():
                    phase_done[phase] = True
                    if phase == 0:
                        print(
                            "\n  Plane 1 done."
                            "\n  Press 's' to save as a flat [camera.FOV] and quit,"
                            "\n  or 'a' to add a second plane at a different height.\n"
                        )
                    else:
                        print(
                            "\n  Both planes done."
                            "\n  Press 's' to save as [camera.FOV.near] + [camera.FOV.far].\n"
                        )

            elif key == ord("a"):
                if not phase_done[0]:
                    print("  Finalise plane 1 first (press 'n').")
                elif phase == 1:
                    print("  Already on plane 2. Collect points and press 'n'.")
                else:
                    phase = 1
                    print(
                        "\n  Move the target to the other height and collect >= 4 boundary points."
                        "\n  Press 'n' when done.\n"
                    )

            elif key == ord("s"):
                if not phase_done[0]:
                    print("  Finalise plane 1 first (press 'n').")
                    continue
                if phase == 1 and not phase_done[1]:
                    print("  Finalise plane 2 first (press 'n').")
                    continue

                fov0 = _compute_fov(points[0])
                z0 = plane_z[0]

                if not phase_done[1]:
                    # Single plane — flat FOV
                    _print_fov("Flat FOV", z0, fov0)
                    ans = (
                        input(f"\n  Write [camera.FOV] to {args.config}? [Y/n] ")
                        .strip()
                        .lower()
                    )
                    if ans != "n":
                        try:
                            _write_fov_to_config(args.config, fov0)
                            print(f"  [camera.FOV] written to {args.config}")
                        except Exception as e:
                            print(f"  WARNING: could not update config: {e}")
                    else:
                        print("  Config not modified.")
                else:
                    # Two planes — frustum FOV, near = lower z
                    fov1 = _compute_fov(points[1])
                    z1 = plane_z[1]
                    if z0 <= z1:
                        near_z, near_fov, far_z, far_fov = z0, fov0, z1, fov1
                    else:
                        near_z, near_fov, far_z, far_fov = z1, fov1, z0, fov0
                    _print_fov("Near plane", near_z, near_fov)
                    _print_fov("Far  plane", far_z, far_fov)
                    ans = (
                        input(
                            f"\n  Write [camera.FOV.near] (z={near_z:.4f} m) and"
                            f" [camera.FOV.far] (z={far_z:.4f} m) to {args.config}? [Y/n] "
                        )
                        .strip()
                        .lower()
                    )
                    if ans != "n":
                        try:
                            _write_frustum_to_config(
                                args.config, near_z, near_fov, far_z, far_fov
                            )
                            print(
                                f"  [camera.FOV.near] and [camera.FOV.far] written to {args.config}"
                            )
                        except Exception as e:
                            print(f"  WARNING: could not update config: {e}")
                    else:
                        print("  Config not modified.")
                break

    finally:
        stop_event.set()
        try:
            cam.stop_acquisition()
            cam.close_device()
        except Exception:
            pass
        try:
            lens.close()
        except Exception:
            pass
        cv2.destroyAllWindows()
        tracker.join(timeout=2)

    print(
        f"\nSession summary:"
        f"\n  Plane 1: {len(points[0])} point(s)"
        + (f"\n  Plane 2: {len(points[1])} point(s)" if points[1] else "")
    )


if __name__ == "__main__":
    main()
