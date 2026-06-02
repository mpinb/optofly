"""Interactive BRAID-to-Ximea calibration with multi-plane FOV.

Requires: Ximea camera connected, BRAID running and tracking.

Usage:
    uv run python -m src.tools.calibrate_braid_ximea
    uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml
    uv run python -m src.tools.calibrate_braid_ximea --out calibrations/braid_to_ximea.npz

Workflow:
    1. Aim a bright laser at positions across the arena.  Press SPACE (auto-detect)
       or LEFT-CLICK (manual) to record each BRAID (x,y,z) <-> pixel (u,v) pair.
       Collect >=6 points spanning 4 corners + interior, varying height.
    2. Press 'f' to fit the DLT projection matrix.
    3. Press 'p' to capture a FOV plane at the current BRAID z (no manual entry).
       Move the target to a different height and press 'p' again to add another plane.
    4. Press 's' to save.  Writes the projection calibration (.npz) and updates
       [camera.FOV] in config.toml.
       * 1 plane  -> flat [camera.FOV]
       * 2 planes -> [camera.FOV.near] + [camera.FOV.far]
    5. Press 'u' to undo the last correspondence point; 'q' to quit.

    At least 6 non-coplanar correspondences are needed for a valid DLT fit.
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

from src.utils.calibration import BraidToXimeaCalibration
from src.utils.config import BraidPublisherConfig

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
    return json.loads(lines[1][len(_DATA_PREFIX) :])


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
# Camera stream
# ---------------------------------------------------------------------------


def _open_ximea_camera(
    width: int = 2112,
    height: int = 2112,
    fps: float = 10.0,
    exposure_us: int = 2000,
) -> tuple:
    """Open and configure the Ximea camera for calibration preview.

    Returns:
        (cam, img, actual_width, actual_height)
    """
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
# Config writers
# ---------------------------------------------------------------------------


def _write_fov_to_config(config_path: str, fov: dict[str, float]) -> None:
    """Overwrite [camera.FOV] x_min/x_max/y_min/y_max values in-place."""
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
    Path(config_path).write_text(text)


def _write_frustum_to_config(
    config_path: str,
    near_z: float,
    near: dict[str, float],
    far_z: float,
    far: dict[str, float],
) -> None:
    """Replace the [camera.FOV] block with [camera.FOV.near] and [camera.FOV.far]."""
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

    fov_end = len(lines)
    for i in range(fov_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and not re.match(r"^\[camera\.FOV", stripped):
            fov_end = i
            break

    new_block = (
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
# FOV computation
# ---------------------------------------------------------------------------


def _compute_fov_at_z(
    calibration: BraidToXimeaCalibration,
    frame_w: int,
    frame_h: int,
    z: float,
) -> tuple[dict[str, float], list[tuple[int, int]]]:
    """Compute FOV dict and the projected pixel corners of the FOV rectangle."""
    fov = calibration.compute_fov(frame_w, frame_h, z)

    corners_world = [
        (fov["x_min"], fov["y_min"], z),
        (fov["x_max"], fov["y_min"], z),
        (fov["x_max"], fov["y_max"], z),
        (fov["x_min"], fov["y_max"], z),
    ]
    fov_pixels = []
    for x, y, zw in corners_world:
        u, v = calibration.project(x, y, zw)
        fov_pixels.append((int(round(u)), int(round(v))))

    return fov, fov_pixels


def _print_fov(label: str, z: float, fov: dict, frame_w: int, frame_h: int) -> None:
    print(f"\n  {label}  z = {z:.4f} m  ({frame_w}x{frame_h} px):")
    print(f"    x_min = {fov['x_min']:.5f}  x_max = {fov['x_max']:.5f}"
          f"  ({(fov['x_max'] - fov['x_min']) * 1000:.1f} mm wide)")
    print(f"    y_min = {fov['y_min']:.5f}  y_max = {fov['y_max']:.5f}"
          f"  ({(fov['y_max'] - fov['y_min']) * 1000:.1f} mm tall)")


# ---------------------------------------------------------------------------
# Overlay drawing
# ---------------------------------------------------------------------------

_GREEN = (0, 220, 0)
_YELLOW = (0, 200, 220)
_WHITE = (240, 240, 240)
_CYAN = (220, 200, 0)
_ORANGE = (0, 140, 255)
_GREY = (120, 120, 120)
_PLANE_COLORS = [_ORANGE, (0, 220, 220), (220, 0, 220), (0, 220, 120)]
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _draw_overlay(
    frame: np.ndarray,
    world_pts: list,
    pixel_pts: list,
    braid_pos: tuple | None,
    reprojection_error: float | None,
    planes: list[tuple[float, dict, list]],
    detected_spot: tuple[float, float] | None,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]

    # Draw each FOV plane rectangle
    for i, (z, fov, fov_pix) in enumerate(planes):
        color = _PLANE_COLORS[i % len(_PLANE_COLORS)]
        if len(fov_pix) == 4:
            pts = np.array(fov_pix, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(vis, [pts], isClosed=True, color=color, thickness=2)
            cv2.putText(
                vis,
                f"Plane {i + 1}  z={z:.3f} m",
                (fov_pix[0][0] + 6, fov_pix[0][1] + 18),
                _FONT,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )

    # Live detected bright-spot centroid
    if detected_spot is not None:
        dx, dy = int(round(detected_spot[0])), int(round(detected_spot[1]))
        cv2.circle(vis, (dx, dy), 18, _CYAN, 2, cv2.LINE_AA)
        cv2.drawMarker(vis, (dx, dy), _CYAN, cv2.MARKER_CROSS, 20, 1)

    # Collected correspondence points
    for i, (pp, wp) in enumerate(zip(pixel_pts, world_pts)):
        u, v = int(pp[0]), int(pp[1])
        cv2.drawMarker(vis, (u, v), _GREEN, cv2.MARKER_CROSS, 14, 2)
        cv2.putText(
            vis,
            f"{i + 1}: ({wp[0]:.3f},{wp[1]:.3f},{wp[2]:.3f})",
            (u + 8, v - 6),
            _FONT,
            0.38,
            _GREEN,
            1,
            cv2.LINE_AA,
        )

    # Status panel (top-left)
    lines: list[tuple[str, tuple]] = [
        (f"Points: {len(world_pts)}/6+", _WHITE),
        (
            f"BRAID: {braid_pos[0]:.3f}, {braid_pos[1]:.3f}, {braid_pos[2]:.3f}"
            if braid_pos
            else "BRAID: no fix",
            _WHITE,
        ),
    ]
    if reprojection_error is not None:
        lines.append((f"RMS reprojection: {reprojection_error:.2f} px", _WHITE))
    for i, (z, fov, _) in enumerate(planes):
        color = _PLANE_COLORS[i % len(_PLANE_COLORS)]
        lines.append((
            f"Plane {i + 1}: z={z:.3f} m  "
            f"x[{fov['x_min']:.3f},{fov['x_max']:.3f}]  "
            f"y[{fov['y_min']:.3f},{fov['y_max']:.3f}]",
            color,
        ))

    for i, (line, color) in enumerate(lines):
        cv2.putText(vis, line, (10, 24 + i * 22), _FONT, 0.52, color, 1, cv2.LINE_AA)

    help_text = "SPACE: auto | CLICK: manual | u: undo | f: fit | p: pin plane @ Braid z | s: save | q: quit"
    cv2.putText(vis, help_text, (10, h - 10), _FONT, 0.45, _YELLOW, 1, cv2.LINE_AA)

    return vis


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Interactive BRAID-to-camera calibration with multi-plane FOV"
    )
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument(
        "--out",
        default="calibrations/braid_to_ximea.npz",
        help="Output projection calibration file",
    )
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--exposure", type=int, default=2000)
    parser.add_argument(
        "--threshold",
        type=int,
        default=200,
        help="Pixel brightness threshold for auto-detecting the laser dot (0-255)",
    )
    args = parser.parse_args()

    try:
        braid_cfg = BraidPublisherConfig(args.config)
    except Exception as e:
        print(f"ERROR: Could not load config from {args.config}: {e}")
        sys.exit(1)

    stop_event = threading.Event()
    try:
        tracker = _BraidTracker(braid_cfg.url, stop_event)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    tracker.start()
    print(f"Connected to Braid SSE at {braid_cfg.url}")

    try:
        cam, img_obj, frame_w, frame_h = _open_ximea_camera(
            fps=args.fps, exposure_us=args.exposure
        )
        print(f"Ximea camera opened: {frame_w}x{frame_h} @ {args.fps} fps")
    except Exception as e:
        print(f"ERROR: Cannot open Ximea camera: {e}")
        stop_event.set()
        sys.exit(1)

    world_pts: list[tuple[float, float, float]] = []
    pixel_pts: list[tuple[float, float]] = []
    calibration = BraidToXimeaCalibration()
    reprojection_error: float | None = None
    # Each plane: (z, fov_dict, fov_pixels)
    planes: list[tuple[float, dict, list]] = []

    gray_buf = np.empty((frame_h, frame_w), dtype=np.uint8)
    click_pending: list[tuple[int, int]] = []
    detected_spot: tuple[float, float] | None = None

    def _on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pending.append((x, y))

    window_name = "BRAID-to-Camera Calibration"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, _on_mouse)

    print(
        "\n--- Instructions ---"
        "\n  Required points (minimum 6 total):"
        "\n    * TOP-LEFT corner of the frame     (1 point)"
        "\n    * TOP-RIGHT corner of the frame    (1 point)"
        "\n    * BOTTOM-LEFT corner of the frame  (1 point)"
        "\n    * BOTTOM-RIGHT corner of the frame (1 point)"
        "\n    * 2+ additional points anywhere inside the frame"
        "\n"
        "\n  Capturing a point:"
        "\n    SPACE      — auto-detect the laser dot centroid (cyan circle = preview)"
        "\n    LEFT-CLICK — manual fallback: records the exact clicked pixel"
        "\n    Both methods snapshot the current BRAID (x, y, z) at capture time."
        "\n"
        "\n  Steps:"
        "\n  1. Collect >=6 correspondences. Press 'f' to fit."
        "\n  2. Position the laser at a desired height. Press 'p' to capture a FOV"
        "\n     plane — the z is read from BRAID automatically (no manual entry)."
        "\n  3. Move to a different height and press 'p' again to add a second plane."
        "\n  4. Press 's' to save the projection calibration and write FOV to config."
        "\n     1 plane -> flat [camera.FOV]"
        "\n     2 planes -> [camera.FOV.near] + [camera.FOV.far]"
        "\n  5. Press 'u' to undo the last correspondence point. 'q' to quit."
        "\n--------------------\n"
    )

    def _record_point(u: float, v: float, source: str) -> None:
        braid_pos = tracker.position
        if braid_pos is None:
            print("  [skip] No BRAID fix — move the target into the tracking volume first")
            return
        world_pts.append(braid_pos)
        pixel_pts.append((float(u), float(v)))
        nonlocal reprojection_error
        reprojection_error = None
        n = len(world_pts)
        print(
            f"  Point {n} [{source}]: "
            f"BRAID ({braid_pos[0]:.4f}, {braid_pos[1]:.4f}, {braid_pos[2]:.4f})"
            f"  ->  pixel ({u:.1f}, {v:.1f})"
        )
        if n < 4:
            print(f"  {4 - n} corner point(s) still needed.")
        elif n == 4:
            print("  All 4 corners collected — add >=2 more interior points.")
        elif n == 6:
            print("  >=6 points collected — press 'f' to fit.")

    def _capture_plane() -> None:
        if not calibration.is_fitted:
            print("  Fit first — press 'f'.")
            return
        braid_pos = tracker.position
        if braid_pos is None:
            print("  [skip] No BRAID fix — move target into tracking volume first.")
            return
        z = braid_pos[2]
        try:
            fov, fov_pix = _compute_fov_at_z(calibration, frame_w, frame_h, z)
        except Exception as e:
            print(f"  FOV computation failed: {e}")
            return
        planes.append((z, fov, fov_pix))
        _print_fov(f"Plane {len(planes)}", z, fov, frame_w, frame_h)
        print(
            f"  Plane {len(planes)} captured at z={z:.4f} m (from BRAID)."
            "\n  Move to another height and press 'p' to add another plane,"
            "\n  or press 's' to save."
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

            if click_pending:
                u, v = click_pending.pop(0)
                _record_point(u, v, "click")

            vis = _draw_overlay(
                frame,
                world_pts,
                pixel_pts,
                tracker.position,
                reprojection_error,
                planes,
                detected_spot,
            )
            cv2.imshow(window_name, vis)

            key = cv2.waitKey(30) & 0xFF

            if key == ord("q"):
                break

            elif key == ord(" "):
                if detected_spot is None:
                    print(
                        f"  [skip] No bright spot detected (threshold={args.threshold}). "
                        "Adjust --threshold or click manually."
                    )
                else:
                    _record_point(detected_spot[0], detected_spot[1], "auto")

            elif key == ord("u"):
                if world_pts:
                    rw = world_pts.pop()
                    rp = pixel_pts.pop()
                    reprojection_error = None
                    print(f"  Undid point {len(world_pts) + 1}: BRAID {rw}  pixel {rp}")
                else:
                    print("  Nothing to undo")

            elif key == ord("f"):
                if len(world_pts) < 6:
                    print(f"  Need >=6 points for DLT fit, have {len(world_pts)}")
                else:
                    try:
                        rms = calibration.fit(np.array(world_pts), np.array(pixel_pts))
                        reprojection_error = rms
                        quality = "good" if rms < 3 else "consider adding more points"
                        print(
                            f"  DLT fit OK — RMS reprojection error: {rms:.2f} px ({quality})"
                            "\n  Press 'p' to capture a FOV plane at the current BRAID z."
                        )
                    except Exception as e:
                        print(f"  Fit failed: {e}")

            elif key == ord("p"):
                _capture_plane()

            elif key == ord("s"):
                # Auto-fit if not yet done
                if not calibration.is_fitted:
                    if len(world_pts) >= 6:
                        try:
                            rms = calibration.fit(
                                np.array(world_pts), np.array(pixel_pts)
                            )
                            reprojection_error = rms
                        except Exception as e:
                            print(f"  Cannot fit before saving: {e}")
                            continue
                    else:
                        print(f"  Need >=6 points to save, have {len(world_pts)}")
                        continue

                # Auto-capture one plane if none recorded yet
                if not planes:
                    print("  No planes captured — capturing one at current BRAID z...")
                    _capture_plane()
                    if not planes:
                        print("  Cannot save: no FOV planes. Press 'p' with a BRAID fix.")
                        continue

                # Save projection calibration
                calibration.save(args.out)
                print(
                    f"\n  Saved projection calibration -> {args.out}"
                    f"  (RMS={reprojection_error:.2f} px, {len(world_pts)} points)"
                )

                # Write FOV to config
                planes_sorted = sorted(planes, key=lambda t: t[0])
                if len(planes_sorted) == 1:
                    z, fov, _ = planes_sorted[0]
                    ans = (
                        input(
                            f"\n  Write flat [camera.FOV] (z={z:.4f} m) to {args.config}? [y/N] "
                        )
                        .strip()
                        .lower()
                    )
                    if ans == "y":
                        try:
                            _write_fov_to_config(args.config, fov)
                            print(f"  [camera.FOV] updated in {args.config}")
                        except Exception as e:
                            print(f"  WARNING: could not update config: {e}")
                    else:
                        print("  Config not modified.")
                else:
                    near_z, near_fov, _ = planes_sorted[0]
                    far_z, far_fov, _ = planes_sorted[-1]
                    if len(planes_sorted) > 2:
                        print(
                            f"  {len(planes_sorted)} planes captured; "
                            f"using z={near_z:.4f} m as near and z={far_z:.4f} m as far."
                        )
                    ans = (
                        input(
                            f"\n  Write [camera.FOV.near] (z={near_z:.4f} m) and "
                            f"[camera.FOV.far] (z={far_z:.4f} m) to {args.config}? [y/N] "
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
        cv2.destroyAllWindows()
        tracker.join(timeout=2)

    if world_pts:
        print(f"\nSession summary: {len(world_pts)} correspondences, {len(planes)} plane(s)")
        if reprojection_error is not None:
            print(f"Final RMS reprojection error: {reprojection_error:.2f} px")
    else:
        print("\nNo correspondences collected.")


if __name__ == "__main__":
    main()
