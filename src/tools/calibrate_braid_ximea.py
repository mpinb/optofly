"""Interactive BRAID-to-Ximea calibration tool.

Requires: Ximea camera connected, BRAID running and tracking.

Usage:
    uv run python -m src.tools.calibrate_braid_ximea
    uv run python -m src.tools.calibrate_braid_ximea --config configs/config.toml
    uv run python -m src.tools.calibrate_braid_ximea --out calibrations/braid_to_ximea.npz

Workflow:
    1. The Ximea camera feed opens in an OpenCV window.
    2. A live BRAID SSE subscriber tracks the current laser/target position (x, y, z).
       No ZMQ stack or main.py needed — the tool connects directly to Braid.
    3. Aim the laser pointer at a position in the arena. Wait for a stable BRAID fix,
       then LEFT-CLICK on the laser dot in the camera window.
       Each click records the current BRAID (x, y, z) and the clicked pixel (u, v).
    4. Collect ≥6 points. For best FOV accuracy, aim the laser near all 4 corners of
       the frame first, then add ≥2 interior points at different heights.
    5. Press 'f' to fit the projection matrix and check reprojection error.
    6. Press 'v' to compute the camera FOV.  The tool asks for a reference height
       (the z value at which the FOV is measured) and draws the projected boundary
       on the frame so you can verify it visually.
    7. Press 's' to save.  The tool asks whether to write the FOV to config.toml.
    8. Press 'u' to undo the last point; 'q' to quit.

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
# Config FOV writer
# ---------------------------------------------------------------------------


def _write_fov_to_config(config_path: str, fov: dict[str, float]) -> None:
    """Overwrite [camera.FOV] x_min/x_max/y_min/y_max values in-place.

    Uses regex so all comments and surrounding formatting are preserved.
    Raises RuntimeError if the [camera.FOV] section is not found.
    """
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


# ---------------------------------------------------------------------------
# Overlay drawing
# ---------------------------------------------------------------------------

_GREEN = (0, 220, 0)
_YELLOW = (0, 200, 220)
_WHITE = (240, 240, 240)
_CYAN = (220, 200, 0)
_ORANGE = (0, 140, 255)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def _draw_overlay(
    frame: np.ndarray,
    world_pts: list,
    pixel_pts: list,
    braid_pos: tuple | None,
    reprojection_error: float | None,
    fov_pixels: list[tuple[int, int]] | None,  # projected FOV corner pixels
    fov: dict | None,
    z_ref: float | None,
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]

    # Draw FOV boundary rectangle (if computed)
    if fov_pixels is not None and len(fov_pixels) == 4:
        pts = np.array(fov_pixels, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(vis, [pts], isClosed=True, color=_ORANGE, thickness=2)
        cv2.putText(
            vis,
            f"FOV boundary @ z={z_ref:.3f} m",
            (fov_pixels[0][0] + 6, fov_pixels[0][1] + 18),
            _FONT, 0.45, _ORANGE, 1, cv2.LINE_AA,
        )

    # Collected correspondence points
    for i, (pp, wp) in enumerate(zip(pixel_pts, world_pts)):
        u, v = int(pp[0]), int(pp[1])
        cv2.drawMarker(vis, (u, v), _GREEN, cv2.MARKER_CROSS, 14, 2)
        cv2.putText(
            vis,
            f"{i+1}: ({wp[0]:.3f},{wp[1]:.3f},{wp[2]:.3f})",
            (u + 8, v - 6),
            _FONT, 0.38, _GREEN, 1, cv2.LINE_AA,
        )

    # Status panel (top-left)
    lines: list[tuple[str, tuple]] = [
        (f"Points: {len(world_pts)}/6+", _WHITE),
        (
            f"BRAID: {braid_pos[0]:.3f}, {braid_pos[1]:.3f}, {braid_pos[2]:.3f}"
            if braid_pos else "BRAID: no fix",
            _WHITE,
        ),
    ]
    if reprojection_error is not None:
        lines.append((f"RMS reprojection: {reprojection_error:.2f} px", _WHITE))
    if fov is not None and z_ref is not None:
        lines.append((f"FOV @ z={z_ref:.3f} m:", _CYAN))
        lines.append((
            f"  x [{fov['x_min']:.4f}, {fov['x_max']:.4f}]  "
            f"({(fov['x_max']-fov['x_min'])*1000:.1f} mm wide)",
            _CYAN,
        ))
        lines.append((
            f"  y [{fov['y_min']:.4f}, {fov['y_max']:.4f}]  "
            f"({(fov['y_max']-fov['y_min'])*1000:.1f} mm tall)",
            _CYAN,
        ))

    for i, (line, color) in enumerate(lines):
        cv2.putText(vis, line, (10, 24 + i * 22), _FONT, 0.52, color, 1, cv2.LINE_AA)

    help_text = "CLICK: add | u: undo | f: fit | v: FOV | s: save | q: quit"
    cv2.putText(vis, help_text, (10, h - 10), _FONT, 0.45, _YELLOW, 1, cv2.LINE_AA)

    return vis


# ---------------------------------------------------------------------------
# FOV computation
# ---------------------------------------------------------------------------


def _ask_z_ref(default: float | None, world_pts: list) -> float:
    """Prompt the user for a z reference height in the terminal."""
    if default is not None:
        return default

    median_z = float(np.median([w[2] for w in world_pts])) if world_pts else 0.2

    print(
        "\n  The FOV depends on the height (z) at which it is measured."
        "\n  Choose a z value near the middle of the arena where flies typically travel."
        f"\n  Your calibration points span z = "
        f"{min(w[2] for w in world_pts):.3f} – {max(w[2] for w in world_pts):.3f} m  "
        f"(median {median_z:.3f} m)."
    )
    raw = input(f"  Enter z reference in metres [default {median_z:.3f}]: ").strip()
    if raw == "":
        return median_z
    try:
        return float(raw)
    except ValueError:
        print(f"  Invalid value, using median {median_z:.3f}")
        return median_z


def _compute_fov(
    calibration: BraidToXimeaCalibration,
    frame_w: int,
    frame_h: int,
    z_ref: float,
) -> tuple[dict, list[tuple[int, int]]]:
    """Compute FOV dict and the projected pixel corners of the FOV rectangle."""
    fov = calibration.compute_fov(frame_w, frame_h, z_ref)

    # Project the four world-space FOV corners back to pixels for visual overlay
    corners_world = [
        (fov["x_min"], fov["y_min"], z_ref),
        (fov["x_max"], fov["y_min"], z_ref),
        (fov["x_max"], fov["y_max"], z_ref),
        (fov["x_min"], fov["y_max"], z_ref),
    ]
    fov_pixels = []
    for x, y, z in corners_world:
        u, v = calibration.project(x, y, z)
        fov_pixels.append((int(round(u)), int(round(v))))

    return fov, fov_pixels


def _print_fov(fov: dict, z_ref: float, frame_w: int, frame_h: int) -> None:
    print(f"\n  Camera FOV at z = {z_ref:.3f} m  ({frame_w}×{frame_h} px):")
    print(f"    x_min = {fov['x_min']:.5f}")
    print(f"    x_max = {fov['x_max']:.5f}")
    print(f"    y_min = {fov['y_min']:.5f}")
    print(f"    y_max = {fov['y_max']:.5f}")
    print(
        f"    width  = {(fov['x_max'] - fov['x_min'])*1000:.1f} mm  "
        f"height = {(fov['y_max'] - fov['y_min'])*1000:.1f} mm"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Interactive BRAID-to-camera calibration (projection + FOV)"
    )
    parser.add_argument("--config", default="configs/config.toml")
    parser.add_argument(
        "--out",
        default="calibrations/braid_to_ximea.npz",
        help="Output projection calibration file",
    )
    parser.add_argument(
        "--fps", type=float, default=10.0, help="Preview frame rate in Hz (default: 10)"
    )
    parser.add_argument(
        "--exposure", type=int, default=2000, help="Exposure time in microseconds (default: 2000)"
    )
    parser.add_argument(
        "--z-ref",
        type=float,
        default=None,
        help="Z height (metres) for FOV computation. "
             "If omitted, the tool prompts you when you press 'v' or 's'.",
    )
    args = parser.parse_args()

    braid_cfg = BraidPublisherConfig(args.config)
    braid_url = braid_cfg.url

    stop_event = threading.Event()
    try:
        tracker = _BraidTracker(braid_url, stop_event)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    tracker.start()
    print(f"Connected to Braid SSE at {braid_url}")

    try:
        cam, img_obj, frame_w, frame_h = _open_ximea_camera(
            fps=args.fps, exposure_us=args.exposure
        )
        print(f"Ximea camera opened: {frame_w}×{frame_h} @ {args.fps} fps")
    except Exception as e:
        print(f"ERROR: Cannot open Ximea camera: {e}")
        stop_event.set()
        sys.exit(1)

    world_pts: list[tuple[float, float, float]] = []
    pixel_pts: list[tuple[float, float]] = []
    calibration = BraidToXimeaCalibration()
    reprojection_error: float | None = None
    fov: dict | None = None
    fov_pixels: list[tuple[int, int]] | None = None
    z_ref: float | None = args.z_ref

    # Pre-allocate grayscale buffer (avoids per-frame malloc)
    gray_buf = np.empty((frame_h, frame_w), dtype=np.uint8)

    click_pending: list[tuple[int, int]] = []

    def _on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            click_pending.append((x, y))

    window_name = "BRAID-to-Camera Calibration"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, _on_mouse)

    print(
        "\n--- Instructions ---"
        "\n  Required points (minimum 6 total):"
        "\n    • TOP-LEFT corner of the frame    (1 point)"
        "\n    • TOP-RIGHT corner of the frame   (1 point)"
        "\n    • BOTTOM-LEFT corner of the frame (1 point)"
        "\n    • BOTTOM-RIGHT corner of the frame(1 point)"
        "\n    • 2+ additional points anywhere inside the frame"
        "\n"
        "\n  The 4 corners are mandatory — they anchor the FOV boundary."
        "\n  The extra interior points give the projection matrix enough"
        "\n  equations to be uniquely determined (DLT needs ≥6)."
        "\n  For best accuracy, vary z height across the set."
        "\n"
        "\n  Steps:"
        "\n  1. Aim the laser pointer at each corner of the camera frame."
        "\n     Wait for a stable BRAID fix, then LEFT-CLICK on it."
        "\n  2. Repeat for 2+ interior positions (any x/y, ideally different z)."
        "\n  3. Press 'f' to fit.  Aim for < 3 px RMS reprojection error."
        "\n  4. Press 'v' to compute and preview the FOV (orange rectangle on frame)."
        "\n  5. Press 's' to save and optionally write the FOV to config.toml."
        "\n  6. Press 'u' to undo the last point.  Press 'q' to quit."
        "\n--------------------"
    )
    if args.z_ref is not None:
        print(f"  FOV will be computed at z = {args.z_ref:.3f} m (--z-ref).")
    print()

    try:
        while True:
            try:
                cam.get_image(img_obj, timeout=2000)
            except Exception as e:
                print(f"Camera read failed: {e}")
                break
            ctypes.memmove(gray_buf.ctypes.data, img_obj.bp, frame_w * frame_h)
            frame = cv2.cvtColor(gray_buf, cv2.COLOR_GRAY2BGR)

            # Process pending clicks
            if click_pending:
                u, v = click_pending.pop(0)
                braid_pos = tracker.position
                if braid_pos is None:
                    print("  [skip] No BRAID fix — move the target into the tracking volume first")
                else:
                    world_pts.append(braid_pos)
                    pixel_pts.append((float(u), float(v)))
                    fov = fov_pixels = None  # invalidate cached FOV
                    print(
                        f"  Point {len(world_pts)}: "
                        f"BRAID ({braid_pos[0]:.4f}, {braid_pos[1]:.4f}, {braid_pos[2]:.4f})"
                        f"  →  pixel ({u}, {v})"
                    )
                    n = len(world_pts)
                    if n < 4:
                        print(f"  {4 - n} corner point(s) still needed.")
                    elif n == 4:
                        print("  All 4 corners collected — add ≥2 more interior points.")
                    elif n == 6:
                        print("  ≥6 points collected — press 'f' to fit.")

            vis = _draw_overlay(
                frame, world_pts, pixel_pts,
                tracker.position, reprojection_error,
                fov_pixels, fov, z_ref,
            )
            cv2.imshow(window_name, vis)

            key = cv2.waitKey(30) & 0xFF

            if key == ord("q"):
                break

            elif key == ord("u"):
                if world_pts:
                    rw = world_pts.pop()
                    rp = pixel_pts.pop()
                    reprojection_error = None
                    fov = fov_pixels = None
                    print(f"  Undid point {len(world_pts)+1}: BRAID {rw}  pixel {rp}")
                else:
                    print("  Nothing to undo")

            elif key == ord("f"):
                if len(world_pts) < 6:
                    print(f"  Need ≥6 points for DLT fit, have {len(world_pts)}")
                else:
                    try:
                        rms = calibration.fit(np.array(world_pts), np.array(pixel_pts))
                        reprojection_error = rms
                        fov = fov_pixels = None
                        quality = "good" if rms < 3 else "consider adding more points"
                        print(f"  DLT fit OK — RMS reprojection error: {rms:.2f} px ({quality})")
                        print("  Press 'v' to compute the camera FOV, 's' to save.")
                    except Exception as e:
                        print(f"  Fit failed: {e}")

            elif key == ord("v"):
                if not calibration.is_fitted:
                    print("  Fit first — press 'f'.")
                elif frame_w is None:
                    print("  No frame yet.")
                else:
                    cur_z = _ask_z_ref(z_ref, world_pts)
                    z_ref = cur_z
                    try:
                        fov, fov_pixels = _compute_fov(calibration, frame_w, frame_h, cur_z)
                        _print_fov(fov, cur_z, frame_w, frame_h)
                        print(
                            "  The orange rectangle on the frame shows the projected FOV boundary."
                            "\n  If it looks correct, press 's' to save."
                        )
                    except Exception as e:
                        print(f"  FOV computation failed: {e}")

            elif key == ord("s"):
                # Fit if needed
                if not calibration.is_fitted:
                    if len(world_pts) >= 6:
                        try:
                            rms = calibration.fit(np.array(world_pts), np.array(pixel_pts))
                            reprojection_error = rms
                            fov = fov_pixels = None
                        except Exception as e:
                            print(f"  Cannot fit before saving: {e}")
                            continue
                    else:
                        print(f"  Need ≥6 points to save, have {len(world_pts)}")
                        continue

                # Save projection matrix
                calibration.save(args.out)
                print(
                    f"\n  Saved projection calibration → {args.out}"
                    f"  (RMS={reprojection_error:.2f} px, {len(world_pts)} points)"
                )

                # Compute FOV (prompts for z_ref if not yet known)
                if frame_w is not None:
                    cur_z = _ask_z_ref(z_ref, world_pts)
                    z_ref = cur_z
                    try:
                        fov, fov_pixels = _compute_fov(calibration, frame_w, frame_h, cur_z)
                        _print_fov(fov, cur_z, frame_w, frame_h)
                    except Exception as e:
                        print(f"  FOV computation failed: {e}")
                        continue

                    # Ask whether to write the FOV to config
                    ans = input(
                        f"\n  Write these FOV values to [camera.FOV] in {args.config}? [y/N] "
                    ).strip().lower()
                    if ans == "y":
                        try:
                            _write_fov_to_config(args.config, fov)
                            print(f"  ✓ [camera.FOV] updated in {args.config}")
                        except Exception as e:
                            print(f"  WARNING: could not update config: {e}")
                            print("  Add the values to [camera.FOV] in config.toml manually.")
                    else:
                        print("  Config not modified.  Add the values manually if needed.")

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
        print(f"\nSession summary: {len(world_pts)} correspondences collected")
        if reprojection_error is not None:
            print(f"Final RMS reprojection error: {reprojection_error:.2f} px")
        if fov is not None and z_ref is not None:
            print(
                f"FOV: x [{fov['x_min']:.4f}, {fov['x_max']:.4f}]  "
                f"y [{fov['y_min']:.4f}, {fov['y_max']:.4f}]  "
                f"@ z = {z_ref:.3f} m"
            )
    else:
        print("\nNo correspondences collected.")


if __name__ == "__main__":
    main()
