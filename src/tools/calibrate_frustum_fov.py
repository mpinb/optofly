"""Frustum FOV calibration tool.

Measures the camera field-of-view at two z-reference planes so that the
perspective-correct frustum trigger zone ([camera.FOV.near] / [camera.FOV.far])
can be populated in config.toml.

Requires: Ximea camera connected, Braid running (HTTP SSE on port 8397), liquid lens
connected with an existing liquid_lens.csv calibration.  No ZMQ stack needed.

Usage:
    uv run python -m src.tools.calibrate_frustum_fov
    uv run python -m src.tools.calibrate_frustum_fov --near-z 0.10 --far-z 0.25
    uv run python -m src.tools.calibrate_frustum_fov --config configs/config.toml

Workflow:
    Phase 1 — Near plane (z_near):
      The tool sets the liquid lens to focus at z_near.
      Move a bright target (LED/laser) to the left, right, top, and bottom
      edges of the camera FOV at approximately z_near height.
      Press SPACE each time to record the current BRAID x,y position.
      Collect ≥4 boundary points (one near each edge), then press 'n'.

    Phase 2 — Far plane (z_far):
      The lens refocuses to z_far.  Repeat the same procedure at z_far height.
      Press 'n' again when done.

    Press 's' to write [camera.FOV.near] and [camera.FOV.far] to config.toml.

    Other keys:  u=undo last point  q=quit without saving
"""

import argparse
import ctypes
import json
import re
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.hardware.lens import LensDriver
from src.processes.lens import setup_lens_calibration
from src.utils.config import BraidPublisherConfig, LiquidLensConfig

_DATA_PREFIX = "data: "
_MAX_RETRIES = 5
_RETRY_DELAY = 2


def _parse_chunk(chunk: str) -> dict:
    lines = chunk.strip().split("\n")
    if len(lines) != 2:
        raise ValueError(f"Expected 2 lines, got {len(lines)}")
    if lines[0] != "event: braid":
        raise ValueError(f"Unexpected event line: {lines[0]!r}")
    if not lines[1].startswith(_DATA_PREFIX):
        raise ValueError(f"Unexpected data line: {lines[1]!r}")
    return json.loads(lines[1][len(_DATA_PREFIX):])


# ---------------------------------------------------------------------------
# BRAID tracker thread (SSE — connects directly to Braid, no ZMQ required)
# ---------------------------------------------------------------------------


class _BraidTracker(threading.Thread):
    def __init__(self, braid_url: str, stop_event: threading.Event) -> None:
        super().__init__(daemon=True, name="braid-tracker")
        self._url = braid_url.rstrip("/")
        self._stop_event = stop_event
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
                    print(f"  BRAID connection lost: {e} — retrying in 1 s")
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
    """Open and configure the Ximea camera.  Returns (cam, img, width, height)."""
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
    offset_x = (offset_x // inc_x) * inc_x
    offset_y = (offset_y // inc_y) * inc_y
    cam.set_offsetX(offset_x)
    cam.set_offsetY(offset_y)

    cam.set_acq_timing_mode("XI_ACQ_TIMING_MODE_FRAME_RATE_LIMIT")
    cam.set_framerate(fps)

    actual_w = cam.get_width()
    actual_h = cam.get_height()
    cam.start_acquisition()
    img = Image()
    return cam, img, actual_w, actual_h


# ---------------------------------------------------------------------------
# Bright-spot detection
# ---------------------------------------------------------------------------


def _detect_bright_spot(
    gray: np.ndarray,
    threshold: int = 200,
    min_area: int = 5,
) -> tuple[float, float] | None:
    """Return centroid (u, v) of the brightest blob, or None if not found."""
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best_u = best_v = None
    best_area = 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area >= min_area and area > best_area:
            best_area = area
            best_u, best_v = float(centroids[i][0]), float(centroids[i][1])
    if best_u is None:
        return None
    return best_u, best_v


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

_PHASE_LABELS = ["Near", "Far"]


def _draw_overlay(
    frame: np.ndarray,
    phase: int,  # 0=near, 1=far
    phase_z: float,
    phase_dpt: float,
    points_near: list[tuple[float, float]],
    points_far: list[tuple[float, float]],
    braid_pos: tuple[float, float, float] | None,
    detected_spot: tuple[float, float] | None,
    both_done: bool,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]

    # Draw completed phase points in grey, current phase points in green
    def _draw_points(pts: list[tuple[float, float]], color: tuple) -> None:
        for i, (px, py) in enumerate(pts):
            u, v = int(round(px)), int(round(py))
            cv2.drawMarker(vis, (u, v), color, cv2.MARKER_CROSS, 14, 2)
            cv2.putText(
                vis,
                f"{i + 1}",
                (u + 8, v - 6),
                _FONT,
                0.38,
                color,
                1,
                cv2.LINE_AA,
            )

    # Draw live bounding-box estimate for current phase's points
    def _draw_bounds_text(pts: list[tuple[float, float]]) -> list[str]:
        if not pts:
            return []
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return [
            f"  x [{min(xs):.4f}, {max(xs):.4f}]  "
            f"({(max(xs) - min(xs)) * 1000:.1f} mm)",
            f"  y [{min(ys):.4f}, {max(ys):.4f}]  "
            f"({(max(ys) - min(ys)) * 1000:.1f} mm)",
        ]

    if phase == 0:
        _draw_points(points_near, _GREEN)
    else:
        _draw_points(points_near, _GREY)
        _draw_points(points_far, _GREEN)

    # Live detected spot
    if detected_spot is not None:
        u, v = int(round(detected_spot[0])), int(round(detected_spot[1]))
        cv2.circle(vis, (u, v), 10, _CYAN, 2)
        cv2.drawMarker(vis, (u, v), _CYAN, cv2.MARKER_CROSS, 20, 1)

    # Phase banner (top-right)
    label = _PHASE_LABELS[phase]
    banner = (
        f"Phase {phase + 1}/2 — {label} plane  z={phase_z:.3f} m  dpt={phase_dpt:+.3f}"
    )
    cv2.putText(vis, banner, (10, 30), _FONT, 0.65, _ORANGE, 2, cv2.LINE_AA)

    # Status panel
    current_pts = points_near if phase == 0 else points_far
    lines: list[tuple[str, tuple]] = [
        (
            f"BRAID: {braid_pos[0]:.3f}, {braid_pos[1]:.3f}, {braid_pos[2]:.3f}"
            if braid_pos
            else "BRAID: no fix",
            _WHITE,
        ),
        (f"Points this phase: {len(current_pts)}/4+", _WHITE),
    ]
    if current_pts:
        lines.append(("Estimated bounds:", _CYAN))
        for ln in _draw_bounds_text(current_pts):
            lines.append((ln, _CYAN))
    if phase == 1 and points_near:
        lines.append((f"Near plane: {len(points_near)} pts (done)", _GREY))

    for i, (line, color) in enumerate(lines):
        cv2.putText(vis, line, (10, 60 + i * 22), _FONT, 0.50, color, 1, cv2.LINE_AA)

    # Help text
    if both_done:
        help_text = "SPACE/CLICK: add | u: undo | s: SAVE | q: quit"
    else:
        help_text = "SPACE/CLICK: add | u: undo | n: next phase (need 4+) | q: quit"
    cv2.putText(vis, help_text, (10, h - 10), _FONT, 0.45, _YELLOW, 1, cv2.LINE_AA)

    return vis


# ---------------------------------------------------------------------------
# Config writer
# ---------------------------------------------------------------------------


def _write_frustum_to_config(
    config_path: str,
    near_z: float,
    near: dict[str, float],
    far_z: float,
    far: dict[str, float],
) -> None:
    """Replace the [camera.FOV] block with [camera.FOV.near] and [camera.FOV.far].

    Finds the [camera.FOV] section and replaces everything up to the next
    top-level or sibling section header, preserving the rest of the file.
    """
    text = Path(config_path).read_text()
    lines = text.splitlines(keepends=True)

    # Find [camera.FOV] line
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

    # Find the next section header that is NOT [camera.FOV.*]
    fov_end = len(lines)
    for i in range(fov_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and not re.match(r"^\[camera\.FOV", stripped):
            fov_end = i
            break

    new_block = (
        "[camera.FOV]\n"
        "# Frustum mode — generated by calibrate_frustum_fov.py\n"
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
# FOV computation
# ---------------------------------------------------------------------------


def _compute_fov(points: list[tuple[float, float]]) -> dict[str, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return {
        "x_min": float(min(xs)),
        "x_max": float(max(xs)),
        "y_min": float(min(ys)),
        "y_max": float(max(ys)),
    }


def _print_fov(label: str, z: float, fov: dict[str, float]) -> None:
    print(f"\n  {label} plane  z = {z:.4f} m:")
    print(
        f"    x_min = {fov['x_min']:.5f}  x_max = {fov['x_max']:.5f}  "
        f"({(fov['x_max'] - fov['x_min']) * 1000:.1f} mm wide)"
    )
    print(
        f"    y_min = {fov['y_min']:.5f}  y_max = {fov['y_max']:.5f}  "
        f"({(fov['y_max'] - fov['y_min']) * 1000:.1f} mm tall)"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _ask_z(prompt: str, default: float | None) -> float:
    if default is not None:
        return default
    while True:
        raw = input(f"  {prompt}: ").strip()
        try:
            return float(raw)
        except ValueError:
            print("  Please enter a number (e.g. 0.10)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure frustum FOV at two z-planes and write to config.toml"
    )
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument(
        "--near-z", type=float, default=None, help="Near z-plane in metres"
    )
    parser.add_argument(
        "--far-z", type=float, default=None, help="Far z-plane in metres"
    )
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--exposure", type=int, default=2000)
    parser.add_argument(
        "--threshold",
        type=int,
        default=200,
        help="Brightness threshold for auto-detect (0–255)",
    )
    args = parser.parse_args()

    # --- Z planes ---
    near_z = _ask_z("Near z-plane in metres (e.g. 0.10)", args.near_z)
    far_z = _ask_z("Far z-plane in metres (e.g. 0.25)", args.far_z)
    if near_z >= far_z:
        print("ERROR: near-z must be less than far-z")
        sys.exit(1)

    # --- Lens calibration ---
    try:
        lens_cfg = LiquidLensConfig(args.config)
    except Exception as e:
        print(f"ERROR: Cannot load liquid lens config: {e}")
        sys.exit(1)

    try:
        lens_cal = setup_lens_calibration(
            lens_cfg.calibration_file, lens_cfg.calibration_model
        )
    except Exception as e:
        print(
            f"ERROR: Cannot load lens calibration from {lens_cfg.calibration_file}: {e}"
        )
        sys.exit(1)

    near_dpt = lens_cal.get_dpt(near_z)
    far_dpt = lens_cal.get_dpt(far_z)
    print(f"\n  Near plane: z={near_z:.4f} m  →  {near_dpt:+.3f} dpt")
    print(f"  Far  plane: z={far_z:.4f} m  →  {far_dpt:+.3f} dpt")

    # --- Lens hardware ---
    try:
        lens = LensDriver(lens_cfg.port)
        lens.to_focal_power_mode()
        print(f"  Lens opened on {lens_cfg.port}")
    except Exception as e:
        print(f"ERROR: Cannot open liquid lens on {lens_cfg.port}: {e}")
        sys.exit(1)

    # --- BRAID tracker (SSE) ---
    braid_cfg = BraidPublisherConfig(args.config)
    stop_event = threading.Event()
    try:
        tracker = _BraidTracker(braid_cfg.url, stop_event)
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
        print(f"  Ximea camera: {frame_w}×{frame_h} @ {args.fps} fps\n")
    except Exception as e:
        print(f"ERROR: Cannot open Ximea camera: {e}")
        stop_event.set()
        lens.close()
        sys.exit(1)

    gray_buf = np.empty((frame_h, frame_w), dtype=np.uint8)

    # --- State ---
    phase = 0  # 0=near, 1=far
    phase_z = [near_z, far_z]
    phase_dpt = [near_dpt, far_dpt]
    points: list[list[tuple[float, float]]] = [[], []]
    both_done = False

    click_pending: list[tuple[int, int]] = []

    def _on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pending.append((x, y))

    window_name = "Frustum FOV Calibration"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, _on_mouse)

    # Set lens to near plane
    lens.set_diopter(phase_dpt[phase])
    print(
        "--- Instructions ---"
        "\n  Phase 1/2 — Near plane"
        f"\n  Lens focused at z={near_z:.3f} m ({near_dpt:+.3f} dpt)."
        "\n  Move a bright target (LED/laser) to the LEFT, RIGHT, TOP, and"
        "\n  BOTTOM edges of the camera frame at approximately that height."
        "\n  Press SPACE each time to record the BRAID x,y position."
        "\n  Collect ≥4 boundary points (one per edge), then press 'n'."
        "\n  Press 'u' to undo, 'q' to quit."
        "\n--------------------\n"
    )

    def _record_point(u: float | None, v: float | None) -> None:
        braid_pos = tracker.position
        if braid_pos is None:
            print("  [skip] No BRAID fix — move target into tracking volume")
            return
        x, y, _ = braid_pos
        points[phase].append((x, y))
        src = f"pixel ({int(u)},{int(v)})" if u is not None else "click"
        print(
            f"  Phase {phase + 1} pt {len(points[phase])}: "
            f"BRAID ({x:.4f}, {y:.4f})  [{src}]"
        )

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

            # Process pending clicks
            if click_pending:
                u, v = click_pending.pop(0)
                _record_point(float(u), float(v))

            vis = _draw_overlay(
                frame,
                phase,
                phase_z[phase],
                phase_dpt[phase],
                points[0],
                points[1],
                tracker.position,
                detected_spot,
                both_done,
            )
            cv2.imshow(window_name, vis)

            key = cv2.waitKey(30) & 0xFF

            if key == ord("q"):
                print("\nQuit without saving.")
                break

            elif key == ord(" "):
                if detected_spot is not None:
                    _record_point(detected_spot[0], detected_spot[1])
                else:
                    print(
                        "  [skip] No bright spot detected — try moving the target or adjusting --threshold"
                    )

            elif key == ord("u"):
                if points[phase]:
                    removed = points[phase].pop()
                    print(f"  Undid point: BRAID ({removed[0]:.4f}, {removed[1]:.4f})")
                else:
                    print("  Nothing to undo in this phase")

            elif key == ord("n"):
                if len(points[phase]) < 4:
                    print(f"  Need ≥4 points to advance (have {len(points[phase])})")
                elif phase == 0:
                    phase = 1
                    lens.set_diopter(phase_dpt[1])
                    both_done = False
                    print(
                        "\n--- Phase 2/2 — Far plane ---"
                        f"\n  Lens refocused to z={far_z:.3f} m ({far_dpt:+.3f} dpt)."
                        "\n  Repeat: move target to the 4 frame edges at this height."
                        "\n  Press SPACE to record, 'n' again when done."
                        "\n----------------------------\n"
                    )
                elif phase == 1:
                    both_done = True
                    print(
                        "\n  Both phases complete."
                        "\n  Press 's' to save to config.toml, or keep adding points."
                        "\n  Press 'u' to undo last point in far phase.\n"
                    )

            elif key == ord("s"):
                if not both_done:
                    if phase == 0:
                        print("  Complete phase 1 first (press 'n' when done)")
                    else:
                        print("  Complete phase 2 first (press 'n' when done)")
                    continue

                near_fov = _compute_fov(points[0])
                far_fov = _compute_fov(points[1])
                _print_fov("Near", near_z, near_fov)
                _print_fov("Far", far_z, far_fov)

                ans = (
                    input(
                        f"\n  Write [camera.FOV.near] and [camera.FOV.far] to {args.config}? [y/N] "
                    )
                    .strip()
                    .lower()
                )
                if ans == "y":
                    try:
                        _write_frustum_to_config(
                            args.config, near_z, near_fov, far_z, far_fov
                        )
                        print(
                            f"  [camera.FOV.near] and [camera.FOV.far] written to {args.config}"
                        )
                    except Exception as e:
                        print(f"  WARNING: could not update config: {e}")
                        print("  Add the values to config.toml manually.")
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
        f"\n  Near plane: {len(points[0])} point(s)"
        f"\n  Far  plane: {len(points[1])} point(s)"
    )


if __name__ == "__main__":
    main()
