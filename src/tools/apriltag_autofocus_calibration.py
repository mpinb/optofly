#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "opencv-contrib-python",
#     "numpy",
#     "scipy",
#     "matplotlib",
#     "pypylon",
#     "pupil-apriltags",
#     "optotune",
#     "ximea",
#     "pyserial",
# ]
#
# [tool.uv.sources]
# optotune = { git = "https://github.com/elhananby/optotune-py" }
# ximea = { git = "ssh://git@github.com/elhananby/ximea-py.git" }
# ///
"""
Calibration tool: collect (z, dpt) pairs using XIMEA autofocus + Basler AprilTag triangulation.

Requires no prior installation — run directly with uv:

    uv run apriltag_autofocus_calibration.py --calibration calib.xml

All dependencies are declared in the script header and fetched automatically by uv.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARDWARE REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • XIMEA camera          — libximea SDK must be installed system-wide
  • Optotune liquid lens  — connected at /dev/optotune_ld
  • Basler camera(s)      — 2+ cameras for AprilTag triangulation (via pypylon)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ARGUMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  --calibration FILE    (required) strand-braid multi_camera_reconstructor XML
  --output FILE         CSV output path
                        default: YYYYMMDD_HHMMSS_liquidlens_calibration.csv
  --tag-family STR      AprilTag family: tag36h11 | tag25h9 | tag16h5 |
                        tagStandard41h12  (default: tag36h11)
  --num-frames N        Basler frames averaged per tag detection (default: 10)
  --roi-size PX         XIMEA ROI side length in pixels, centred (default: 1024)
  --exposure US         XIMEA exposure in microseconds (default: 5000)
  --sweeps K            Full autofocus sweeps per position; results are averaged
                        with IQR outlier rejection (default: 5)
  --motor-port PATH     USB serial port for Arduino/GRBL motor stage
                        (e.g., /dev/ttyACM0). When set → automated mode.
  --measurements N      Number of positions across the full 200 mm range
                        (default: 20). Step size = 200.0 / N.
                        Only used with --motor-port.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Manual mode (default, --motor-port absent):
    1. A live XIMEA preview opens.
         SPACE — confirm tag is in place and start measurement
         Q / ESC — quit

    2. Autofocus runs (--sweeps times):
         • Coarse sweep across full diopter range (0.4 dpt steps)
         • Fine sweep around the peak (0.04 dpt steps, slower settle time)
         • Lorentzian fit → best-focus diopter
         • Repeats K times; median / IQR-filtered average returned
         • A blocking plot of the last sweep is shown; close it to continue

    3. Basler cameras capture --num-frames frames, detect all visible AprilTags,
       triangulate each tag's 3-D position, and print Z + reprojection error.
       If multiple tags are visible the mean Z is used.

    4. Result is shown:  z=X.XXXX m  dpt=Y.YYYY
         Enter — accept and save to CSV
         R     — retry autofocus + detection at the same position
         Q     — quit without saving this point

    5. Prompt to move to the next position (Enter / Q to finish).

  Automated mode (--motor-port set):
    • Position the tag at the bottom, press Enter.
    • Script runs autofocus → tag detection → save → motor move up → repeat
      automatically for --measurements positions.
    • No interactive prompts per position. Failed detections are skipped
      with a warning.
    • Ctrl+C at any time saves and plots whatever was collected so far.

  After collection, linear and inverse regression models are fitted and plotted:
    linear:   z = m·dpt + q
    inverse:  z = a / (dpt − b) + c   ← expected for front-lens configuration

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT CSV FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  z,dpt
  0.350000,-1.234500      # z in metres, dpt = best-focus diopter
  0.500000,-0.876500
"""

from __future__ import annotations

import argparse
import csv
import os
import time
import warnings
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Suppress Qt "Cannot find font directory" warnings emitted by opencv-contrib's
# bundled Qt backend before any GUI window is created.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts.warning=false")

import cv2
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import curve_fit


# ---------------------------------------------------------------------------
# Contrast metric
# ---------------------------------------------------------------------------


def laplacian_variance(image: np.ndarray) -> float:
    """Variance of the Laplacian after Gaussian pre-blur."""
    blurred = cv2.GaussianBlur(image, (7, 7), 0)
    lap = cv2.Laplacian(blurred, cv2.CV_64F)
    return float(lap.var())


# ---------------------------------------------------------------------------
# Lorentzian model
# ---------------------------------------------------------------------------


def lorentzian(
    x: np.ndarray, A: float, gamma: float, x0: float, c: float
) -> np.ndarray:
    """Lorentzian: (A/π)·γ / ((x−x0)² + γ²) + c"""
    return (A / np.pi) * gamma / ((x - x0) ** 2 + gamma**2) + c


def fit_lorentzian(dpts: np.ndarray, vals: np.ndarray) -> tuple[dict[str, float], bool]:
    """Fit a Lorentzian to sweep data. Returns (params_dict, success)."""
    idx_max = int(np.argmax(vals))
    x0_g = dpts[idx_max]
    c_g = float(np.min(vals))
    gamma_g = 0.5
    A_g = (float(vals[idx_max]) - c_g) * np.pi * gamma_g
    p0 = [A_g, gamma_g, x0_g, c_g]
    bounds = (
        [0, 0.01, float(dpts.min()) - 1.0, 0],
        [np.inf, 5.0, float(dpts.max()) + 1.0, np.inf],
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            popt, _ = curve_fit(
                lorentzian, dpts, vals, p0=p0, bounds=bounds, maxfev=5000
            )
        return {"A": popt[0], "gamma": popt[1], "x0": popt[2], "c": popt[3]}, True
    except (RuntimeError, ValueError):
        return {"A": A_g, "gamma": gamma_g, "x0": x0_g, "c": c_g}, False


# ---------------------------------------------------------------------------
# Motor stage (GRBL over USB serial)
# ---------------------------------------------------------------------------


class MotorStage:
    """Thin wrapper over an Arduino running GRBL, single-axis via USB serial."""

    def __init__(self, port: str) -> None:
        import serial

        self._ser = serial.Serial(port, 115200, timeout=2.0)
        time.sleep(2.0)  # let GRBL boot
        self._command("$X")  # unlock
        self._command("G91")  # relative positioning

    def _command(self, gcode: str, timeout_s: float = 10.0) -> str:
        """Send a G-code line, return the last response line (blocks until ok/error)."""
        self._ser.write(f"{gcode}\n".encode())
        deadline = time.monotonic() + timeout_s
        response = ""
        while time.monotonic() < deadline:
            line = self._ser.readline().decode().strip()
            if not line:
                continue
            response = line
            if line.startswith("ok") or line.startswith("error"):
                break
        else:
            raise RuntimeError(
                f"GRBL did not respond within {timeout_s:.0f} s to: {gcode}"
            )
        return response

    def move_up_mm(self, distance_mm: float, feed_rate: float = 400.0) -> None:
        """Move upward by distance_mm at feed_rate mm/min."""
        resp = self._command(f"G0 Y{distance_mm:.3f} F{feed_rate:.0f}")
        if resp.startswith("error"):
            raise RuntimeError(f"GRBL move error: {resp}")

    def close(self) -> None:
        self._ser.close()


# ---------------------------------------------------------------------------
# Diopter sweep
# ---------------------------------------------------------------------------

# Hardware settle time after each diopter change (seconds)
COARSE_SETTLE_S = 0.05
FINE_SETTLE_S = 0.10


def sweep(
    cam: Any,
    img: Any,
    lens: Any,
    diopters: np.ndarray,
    roi_slice: tuple[slice, slice],
    settle_s: float = COARSE_SETTLE_S,
    display_window: str | None = None,
) -> np.ndarray:
    """Set each diopter, grab two frames (discard first for freshness), compute contrast."""
    contrasts = np.empty(len(diopters))
    for i, dpt in enumerate(diopters):
        lens.set_diopter(float(dpt))
        time.sleep(settle_s)
        cam.get_image(img)  # discard stale frame
        cam.get_image(img)
        full = img.get_image_data_numpy()
        roi_frame = full[roi_slice].copy()
        contrasts[i] = laplacian_variance(roi_frame)
        if display_window is not None:
            _show_roi(roi_frame)
            cv2.waitKey(1)
    return contrasts


# ---------------------------------------------------------------------------
# Live preview
# ---------------------------------------------------------------------------

PREVIEW_SCALE = 0.25  # downsample factor for display
PREVIEW_WAIT_MS = 100  # cv2.waitKey interval — ~10 fps
PREVIEW_WINDOW = "XIMEA Preview — SPACE=confirm  Q=quit"


def _show_roi(frame: np.ndarray) -> None:
    """Resize and display a ROI frame in the shared preview window."""
    display = cv2.resize(
        frame,
        (int(frame.shape[1] * PREVIEW_SCALE), int(frame.shape[0] * PREVIEW_SCALE)),
    )
    cv2.imshow(PREVIEW_WINDOW, display)


def run_preview_loop(cam: Any, img: Any, roi_slice: tuple[slice, slice]) -> bool:
    """Show a live XIMEA preview in a cv2 popup.

    Returns True if user pressed SPACE to confirm placement (window stays open
    for use during autofocus), False if user pressed Q or closed the window.
    """
    print("\nPosition the AprilTag, then press SPACE to confirm or Q to quit.")
    cv2.namedWindow(PREVIEW_WINDOW, cv2.WINDOW_NORMAL)

    while True:
        cam.get_image(img)
        frame = img.get_image_data_numpy()[roi_slice].copy()
        _show_roi(frame)

        key = cv2.waitKey(PREVIEW_WAIT_MS) & 0xFF
        if key == ord(" "):
            return True  # keep window open for autofocus display
        if key in (ord("q"), ord("Q"), 27):  # Q or ESC
            cv2.destroyWindow(PREVIEW_WINDOW)
            return False
        if cv2.getWindowProperty(PREVIEW_WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            return False


# ---------------------------------------------------------------------------
# Autofocus constants
# ---------------------------------------------------------------------------

MIN_DPT = -2.0
MAX_DPT = 3.0
COARSE_STEP = 0.4
FINE_STEP = 0.04
FINE_HALF_RANGE = 0.8


# ---------------------------------------------------------------------------
# Autofocus orchestration
# ---------------------------------------------------------------------------


def _average_excluding_outliers(values: list[float]) -> float:
    """Return robust central estimate of values.

    For n >= 4: IQR-based outlier removal, then mean of survivors.
    For n < 4: median (immune to a single outlier regardless of n).
    """
    arr = np.array(values, dtype=float)
    if len(arr) < 4:
        return float(np.median(arr))
    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    mask = (arr >= q1 - 1.5 * iqr) & (arr <= q3 + 1.5 * iqr)
    kept = arr[mask]
    return float(np.mean(kept)) if len(kept) > 0 else float(np.median(arr))


def _run_single_sweep(
    cam: Any,
    img: Any,
    lens: Any,
    roi_slice: tuple[slice, slice],
    display_window: str | None = None,
) -> tuple[float, np.ndarray, np.ndarray, dict[str, float], bool]:
    """One full coarse+fine sweep. Returns (best_dpt, all_dpts, all_vals, params, fit_ok)."""
    coarse_dpts = np.arange(MIN_DPT, MAX_DPT + COARSE_STEP * 0.5, COARSE_STEP)
    coarse_vals = sweep(cam, img, lens, coarse_dpts, roi_slice, display_window=display_window)
    coarse_peak_idx = int(np.argmax(coarse_vals))
    coarse_peak_dpt = float(coarse_dpts[coarse_peak_idx])

    fine_lo = max(MIN_DPT, coarse_peak_dpt - FINE_HALF_RANGE)
    fine_hi = min(MAX_DPT, coarse_peak_dpt + FINE_HALF_RANGE)
    fine_dpts = np.arange(fine_lo, fine_hi + FINE_STEP * 0.5, FINE_STEP)
    fine_vals = sweep(cam, img, lens, fine_dpts, roi_slice, settle_s=FINE_SETTLE_S, display_window=display_window)

    all_dpts = np.concatenate([coarse_dpts, fine_dpts])
    all_vals = np.concatenate([coarse_vals, fine_vals])
    order = np.argsort(all_dpts)
    all_dpts, all_vals = all_dpts[order], all_vals[order]

    params, fit_ok = fit_lorentzian(all_dpts, all_vals)
    best_dpt = params["x0"] if fit_ok else coarse_peak_dpt
    return best_dpt, all_dpts, all_vals, params, fit_ok


def run_autofocus(
    cam: Any,
    img: Any,
    lens: Any,
    roi_slice: tuple[slice, slice],
    k: int = 1,
    display_window: str | None = None,
    show_plot: bool = True,
) -> float:
    """Run coarse + fine sweep k times, average best-focus diopters (IQR outlier rejection).

    The final plot shows the last sweep's contrast data with the averaged diopter marked.
    Returns the (averaged) best-focus diopter.
    """
    best_dpts: list[float] = []
    last_dpts = last_vals = last_params = None
    last_fit_ok = False

    for i in range(k):
        print(f"\nSweep {i + 1}/{k}")
        best_dpt, all_dpts, all_vals, params, fit_ok = _run_single_sweep(
            cam, img, lens, roi_slice, display_window=display_window
        )
        print(f"  best focus: {best_dpt:+.4f} dpt")
        best_dpts.append(best_dpt)
        last_dpts, last_vals, last_params, last_fit_ok = (
            all_dpts,
            all_vals,
            params,
            fit_ok,
        )

    if k > 1:
        averaged = _average_excluding_outliers(best_dpts)
        print(f"\nFinal: {averaged:+.4f} dpt (avg of {k}, IQR filtered)")
        best_dpt_final = averaged
    else:
        best_dpt_final = best_dpts[0]

    # Plot last sweep + final averaged diopter
    fit_x = np.linspace(last_dpts[0], last_dpts[-1], 500)
    fit_y = lorentzian(
        fit_x,
        last_params["A"],
        last_params["gamma"],
        last_params["x0"],
        last_params["c"],
    )

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(
        last_dpts,
        last_vals,
        s=20,
        color="tab:blue",
        label="Data (last sweep)",
        zorder=3,
    )
    if last_fit_ok:
        ax.plot(
            fit_x, fit_y, color="tab:orange", lw=2, label="Lorentzian fit (last sweep)"
        )
    ax.axvline(
        best_dpt_final,
        color="tab:red",
        ls="--",
        alpha=0.8,
        label=f"Best focus: {best_dpt_final:.3f} dpt"
        + (f" (avg of {k})" if k > 1 else ""),
    )
    ax.set_xlabel("Optical power [dpt]")
    ax.set_ylabel("Contrast (Laplacian variance)")
    ax.set_title("Autofocus sweep" + (f" ({k} sweeps averaged)" if k > 1 else ""))
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    if display_window is not None:
        cv2.destroyWindow(display_window)

    if show_plot:
        print("(close the sweep plot window to continue)")
        plt.show()  # blocking

    return best_dpt_final


# ---------------------------------------------------------------------------
# AprilTag detection pipeline
# ---------------------------------------------------------------------------


@dataclass
class CameraCalibration:
    cam_id: str
    projection_matrix: np.ndarray  # 3x4
    resolution: tuple[int, int]
    fx: float
    fy: float
    cx: float
    cy: float
    dist_coeffs: np.ndarray  # (k1, k2, p1, p2)
    camera_matrix: np.ndarray  # 3x3


def parse_calibration_xml(xml_path: str) -> dict[str, CameraCalibration]:
    """Parse a strand-braid multi_camera_reconstructor XML calibration file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    cameras: dict[str, CameraCalibration] = {}
    for sc in root.findall("single_camera_calibration"):
        cam_id = sc.find("cam_id").text.strip()
        pmat_text = sc.find("calibration_matrix").text.strip()
        rows = pmat_text.split(";")
        P = np.array([[float(v) for v in r.split()] for r in rows])
        res_text = sc.find("resolution").text.strip().split()
        resolution = (int(res_text[0]), int(res_text[1]))
        nlp = sc.find("non_linear_parameters")
        fx = float(nlp.find("fc1").text)
        fy = float(nlp.find("fc2").text)
        cx_ = float(nlp.find("cc1").text)
        cy_ = float(nlp.find("cc2").text)
        k1 = float(nlp.find("k1").text)
        k2 = float(nlp.find("k2").text)
        p1 = float(nlp.find("p1").text)
        p2 = float(nlp.find("p2").text)
        K = np.array([[fx, 0, cx_], [0, fy, cy_], [0, 0, 1]])
        cameras[cam_id] = CameraCalibration(
            cam_id=cam_id,
            projection_matrix=P,
            resolution=resolution,
            fx=fx,
            fy=fy,
            cx=cx_,
            cy=cy_,
            dist_coeffs=np.array([k1, k2, p1, p2]),
            camera_matrix=K,
        )
    return cameras


def capture_from_open_cameras(
    basler_cams: list[Any],
    cam_ids: list[str],
    n: int,
) -> dict[str, np.ndarray]:
    """Average n frames from each pre-opened Basler camera."""
    from pypylon import pylon

    images: dict[str, np.ndarray] = {}
    for cam_id, camera in zip(cam_ids, basler_cams):
        accumulated = None
        count = 0
        camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
        for _ in range(n):
            grab = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grab.GrabSucceeded():
                frame = grab.Array.astype(np.float64)
                accumulated = frame if accumulated is None else accumulated + frame
                count += 1
            grab.Release()
        camera.StopGrabbing()
        if count > 0:
            images[cam_id] = (accumulated / count).astype(np.uint8)
    return images


_ARUCO_FAMILIES = {
    "tag36h11": cv2.aruco.DICT_APRILTAG_36h11,
    "tag25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "tag16h5": cv2.aruco.DICT_APRILTAG_16h5,
}
_PUPIL_FAMILIES = {"tagStandard41h12"}


def detect_apriltags(
    images: dict[str, np.ndarray], tag_family: str
) -> dict[str, list[tuple[int, float, float]]]:
    """Detect AprilTags in each image. Returns {cam_id: [(tag_id, cx, cy)]}."""
    if tag_family in _ARUCO_FAMILIES:
        return _detect_aruco(images, tag_family)
    if tag_family in _PUPIL_FAMILIES:
        return _detect_pupil(images, tag_family)
    raise ValueError(f"Unknown tag family: {tag_family!r}")


def _detect_aruco(
    images: dict[str, np.ndarray], tag_family: str
) -> dict[str, list[tuple[int, float, float]]]:
    params = cv2.aruco.DetectorParameters()
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 53
    params.adaptiveThreshWinSizeStep = 5
    params.minMarkerPerimeterRate = 0.01
    params.polygonalApproxAccuracyRate = 0.05
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(_ARUCO_FAMILIES[tag_family]), params
    )
    out: dict[str, list[tuple[int, float, float]]] = {}
    for cam_id, img in images.items():
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = detector.detectMarkers(gray)
        dets = []
        if ids is not None:
            for i, tid in enumerate(ids.flatten()):
                cx, cy = corners[i][0].mean(axis=0)
                dets.append((int(tid), float(cx), float(cy)))
        out[cam_id] = dets
    return out


def _detect_pupil(
    images: dict[str, np.ndarray], tag_family: str
) -> dict[str, list[tuple[int, float, float]]]:
    from pupil_apriltags import Detector

    detector = Detector(families=tag_family)
    out: dict[str, list[tuple[int, float, float]]] = {}
    for cam_id, img in images.items():
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dets = [
            (int(r.tag_id), float(r.center[0]), float(r.center[1]))
            for r in detector.detect(gray)
        ]
        out[cam_id] = dets
    return out


def _undistort_points(pts: np.ndarray, cal: CameraCalibration) -> np.ndarray:
    p = pts.reshape(-1, 1, 2).astype(np.float64)
    return cv2.undistortPoints(
        p, cal.camera_matrix, cal.dist_coeffs, P=cal.camera_matrix
    ).reshape(-1, 2)


def triangulate_points(
    observations: list[tuple[CameraCalibration, np.ndarray]],
) -> np.ndarray:
    """DLT triangulation from 2+ (calibration, pixel_xy) pairs. Returns (x, y, z)."""
    A = []
    for cal, pt in observations:
        P = cal.projection_matrix
        x, y = float(pt[0]), float(pt[1])
        A.append(x * P[2, :] - P[0, :])
        A.append(y * P[2, :] - P[1, :])
    _, _, Vt = np.linalg.svd(np.array(A))
    X = Vt[-1]
    return X[:3] / X[3]


# ---------------------------------------------------------------------------
# Tag detection orchestration
# ---------------------------------------------------------------------------


def run_tag_detection(
    basler_cams: list[Any],
    cam_ids: list[str],
    cameras_cal: dict[str, CameraCalibration],
    n_frames: int,
    tag_family: str,
) -> float | None:
    """Capture from Basler cameras, detect AprilTag, triangulate.

    Returns Z in metres, or None if triangulation is not possible
    (tag not seen by 2+ cameras).
    """
    print(f"\nCapturing {n_frames} frames from {len(basler_cams)} Basler camera(s)...")
    images = capture_from_open_cameras(basler_cams, cam_ids, n_frames)

    print("Detecting AprilTags...")
    detections = detect_apriltags(images, tag_family)

    # Group by tag ID
    tag_obs: dict[int, list[tuple[CameraCalibration, np.ndarray]]] = {}
    for cam_id, dets in detections.items():
        if cam_id not in cameras_cal:
            continue
        cal = cameras_cal[cam_id]
        for tag_id, cx, cy in dets:
            undist = _undistort_points(np.array([[cx, cy]]), cal)[0]
            tag_obs.setdefault(tag_id, []).append((cal, undist))

    if not tag_obs:
        print("  No AprilTags detected — skipping this position.")
        return None

    # Triangulate every tag seen by 2+ cameras, then average their Z values
    z_values: list[float] = []
    for tag_id, obs in sorted(tag_obs.items()):
        if len(obs) < 2:
            print(f"  Tag {tag_id} seen by only 1 camera — cannot triangulate.")
            continue
        xyz = triangulate_points(obs)
        z = float(xyz[2])

        X_h = np.append(xyz, 1.0)
        errors = [
            float(
                np.linalg.norm(
                    (cal.projection_matrix @ X_h)[:2] / (cal.projection_matrix @ X_h)[2]
                    - obs_pt
                )
            )
            for cal, obs_pt in obs
        ]
        reproj_err = float(np.mean(errors))

        print(
            f"  Tag {tag_id}: Z={z:.4f} m  reprojection error={reproj_err:.2f} px  ({len(obs)} cameras)"
        )
        z_values.append(z)

    if not z_values:
        print("  No tag seen by 2+ cameras — skipping this position.")
        return None

    z_final = float(np.mean(z_values))
    if len(z_values) > 1:
        print(f"  Mean Z across {len(z_values)} tags: {z_final:.4f} m")
    return z_final


# ---------------------------------------------------------------------------
# Regression models
# ---------------------------------------------------------------------------


def _r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - np.mean(y_true)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def _fit_linear(dpts: np.ndarray, zs: np.ndarray) -> tuple[float, float, float]:
    """Fit z = m*dpt + q. Returns (m, q, R²)."""
    m, q = np.polyfit(dpts, zs, deg=1)
    r2 = _r2(zs, m * dpts + q)
    return float(m), float(q), r2


def _inverse_func(x: np.ndarray, a: float, b: float, c: float) -> np.ndarray:
    return a / (x - b) + c


def _fit_inverse(dpts: np.ndarray, zs: np.ndarray) -> tuple[float, float, float, float]:
    """Fit z = a/(dpt - b) + c. Returns (a, b, c, R²). Tries b outside dpt range."""
    dpt_max = float(dpts.max())
    dpt_min = float(dpts.min())
    for p0, bounds in [
        (
            [-500.0, dpt_max + 1.0, float(zs.min())],
            ([-np.inf, dpt_max + 0.1, -np.inf], [np.inf, dpt_max + 50.0, np.inf]),
        ),
        (
            [500.0, dpt_min - 1.0, float(zs.min())],
            ([-np.inf, dpt_min - 50.0, -np.inf], [np.inf, dpt_min - 0.1, np.inf]),
        ),
    ]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                popt, _ = curve_fit(
                    _inverse_func, dpts, zs, p0=p0, bounds=bounds, maxfev=10_000
                )
            a, b, c = popt
            r2 = _r2(zs, _inverse_func(dpts, a, b, c))
            return float(a), float(b), float(c), r2
        except (RuntimeError, ValueError):
            continue
    raise RuntimeError(
        "Inverse model fit failed for both b > max(dpt) and b < min(dpt)"
    )


def fit_and_plot(zs: np.ndarray, dpts: np.ndarray) -> None:
    """Fit linear and inverse models; print R²; show scatter + fit plot."""
    print("\n" + "=" * 50)
    print("Regression fits (dpt → z)")
    print("=" * 50)

    # Linear
    try:
        m, q, r2_lin = _fit_linear(dpts, zs)
        print(f"  Linear:  z = {m:.4f}·dpt + {q:.4f}   R²={r2_lin:.4f}")
    except Exception as e:
        print(f"  Linear fit failed: {e}")
        m = q = r2_lin = None

    # Inverse
    try:
        a, b, c, r2_inv = _fit_inverse(dpts, zs)
        print(f"  Inverse: z = {a:.4f}/(dpt - {b:.4f}) + {c:.4f}   R²={r2_inv:.4f}")
    except Exception as e:
        print(f"  Inverse fit failed: {e}")
        a = b = c = r2_inv = None

    # Plot
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(dpts, zs, s=40, color="tab:blue", zorder=3, label="Measurements")

    dpt_dense = np.linspace(dpts.min(), dpts.max(), 300)
    if m is not None:
        ax.plot(
            dpt_dense,
            m * dpt_dense + q,
            color="tab:orange",
            lw=2,
            label=f"Linear R²={r2_lin:.3f}",
        )
    if a is not None:
        ax.plot(
            dpt_dense,
            _inverse_func(dpt_dense, a, b, c),
            color="tab:green",
            lw=2,
            ls="--",
            label=f"Inverse R²={r2_inv:.3f}",
        )

    ax.set_xlabel("Best-focus diopter [dpt]")
    ax.set_ylabel("Z position [m]")
    ax.set_title("Diopter → Z calibration")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect (z, dpt) calibration pairs via autofocus + AprilTag triangulation"
    )
    p.add_argument(
        "--calibration",
        required=True,
        help="Path to strand-braid XML calibration file for Basler cameras",
    )
    p.add_argument(
        "--output",
        default=None,
        help="CSV output path (default: YYYYMMDD_HHMMSS_liquidlens_calibration.csv)",
    )
    p.add_argument(
        "--tag-family",
        default="tag36h11",
        choices=["tag36h11", "tag25h9", "tag16h5", "tagStandard41h12"],
        help="AprilTag family (default: tag36h11)",
    )
    p.add_argument(
        "--num-frames",
        type=int,
        default=10,
        help="Basler frames to average per detection (default: 10)",
    )
    p.add_argument(
        "--roi-size",
        type=int,
        default=1024,
        help="XIMEA ROI size in pixels, centered square (default: 1024)",
    )
    p.add_argument(
        "--exposure",
        type=int,
        default=5000,
        help="XIMEA exposure in microseconds (default: 5000)",
    )
    p.add_argument(
        "--sweeps",
        type=int,
        default=5,
        help="Number of full autofocus sweeps to average (default: 5)",
    )
    p.add_argument(
        "--motor-port",
        default=None,
        help="USB serial port for Arduino/GRBL motor stage (e.g., /dev/ttyACM0). "
        "When set, runs in automated mode — motor moves the AprilTag upward between "
        "measurements instead of prompting the user to reposition manually.",
    )
    p.add_argument(
        "--measurements",
        type=int,
        default=20,
        help="Number of positions to measure across the full 200 mm vertical range "
        "(default: 20). Step size = 200.0 / measurements. Only used with --motor-port.",
    )
    return p


def _sort_csv(path: Path) -> None:
    """Re-read CSV, sort rows by z ascending, overwrite."""
    with open(path, newline="") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: float(r["z"]))
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["z", "dpt"])
        writer.writeheader()
        writer.writerows(rows)


def _init_csv(path: Path) -> None:
    """Write CSV header if the file does not already exist."""
    if not path.exists():
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(["z", "dpt"])


def _append_csv(path: Path, z: float, dpt: float) -> None:
    """Append one (z, dpt) row to the CSV."""
    with open(path, "a", newline="") as f:
        csv.writer(f).writerow([f"{z:.6f}", f"{dpt:.6f}"])


def _run_automated(
    args: argparse.Namespace,
    output_path: Path,
    cameras_cal: dict[str, CameraCalibration],
    open_ids: list[str],
    basler_cams: list[Any],
    ximea_cam: Any,
    ximea_img: Any,
    lens: Any,
    roi_slice: tuple[slice, slice],
    motor: MotorStage,
) -> None:
    """Automated calibration: motor moves the tag upward, autofocus at each position."""
    step_size_mm = 200.0 / args.measurements

    input("Position the AprilTag at the bottom of the range, then press Enter to start. ")
    collected: list[tuple[float, float]] = []
    failed: list[int] = []

    try:
        for i in range(args.measurements):
            print(f"\n--- [{i + 1}/{args.measurements}] ---")

            best_dpt = run_autofocus(
                ximea_cam, ximea_img, lens, roi_slice,
                k=args.sweeps, show_plot=False,
            )

            z = run_tag_detection(
                basler_cams, open_ids, cameras_cal, args.num_frames, args.tag_family
            )

            if z is None:
                print("  WARNING: no valid Z — skipping this position.")
                failed.append(i + 1)
            else:
                print(f"  Result: z={z:.4f} m  dpt={best_dpt:.4f}")
                _append_csv(output_path, z, best_dpt)
                collected.append((z, best_dpt))

            if i < args.measurements - 1:
                print(f"  Moving motor up by {step_size_mm:.2f} mm...")
                motor.move_up_mm(step_size_mm)
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nInterrupted — proceeding with collected data.")

    if failed:
        print(f"\nWARNING: {len(failed)} position(s) skipped due to failed detection: {failed}")

    if collected:
        _sort_csv(output_path)
        print(f"CSV sorted by z: {output_path}")

        zs = np.array([p[0] for p in collected])
        dpts = np.array([p[1] for p in collected])
        fit_and_plot(zs, dpts)
    else:
        print("\nNo data collected — cannot fit regression models.")


def main() -> None:
    from optotune import LiquidLens
    from pypylon import pylon
    from ximea import xiapi

    args = _build_parser().parse_args()
    if args.output is None:
        args.output = time.strftime("%Y%m%d_%H%M%S") + "_liquidlens_calibration.csv"
    output_path = Path(args.output)

    # --- Parse calibration XML ---
    print(f"Parsing calibration: {args.calibration}")
    cameras_cal = parse_calibration_xml(args.calibration)
    cam_ids = list(cameras_cal.keys())
    print(f"  Found {len(cam_ids)} camera(s): {cam_ids}")

    # --- Open Basler cameras ---
    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()
    device_map = {}
    for dev in devices:
        serial = dev.GetSerialNumber()
        for cid in cam_ids:
            if cid.endswith(serial):
                device_map[cid] = dev
    basler_cams = [
        pylon.InstantCamera(tlf.CreateDevice(device_map[cid]))
        for cid in cam_ids
        if cid in device_map
    ]
    open_ids = [cid for cid in cam_ids if cid in device_map]
    for cam in basler_cams:
        cam.Open()
    print(f"  Opened {len(basler_cams)} Basler camera(s)")

    # --- Open XIMEA camera ---
    ximea_cam = xiapi.Camera()
    ximea_cam.open_device()
    ximea_cam.set_imgdataformat("XI_MONO8")
    ximea_cam.set_exposure(args.exposure)
    ximea_cam.enable_recent_frame()
    ximea_cam.set_buffers_queue_size(2)
    ximea_cam.enable_bpc()
    ximea_cam.set_column_fpn_correction("XI_ON")
    ximea_cam.start_acquisition()

    ximea_img = xiapi.Image()
    ximea_cam.get_image(ximea_img)
    sensor_w, sensor_h = ximea_img.width, ximea_img.height
    roi_sz = min(args.roi_size, sensor_w, sensor_h)
    roi_x = (sensor_w - roi_sz) // 2
    roi_y = (sensor_h - roi_sz) // 2
    roi_slice = np.s_[roi_y : roi_y + roi_sz, roi_x : roi_x + roi_sz]
    print(f"  XIMEA sensor: {sensor_w}x{sensor_h}  ROI: {roi_sz}x{roi_sz} centred")

    # --- Open liquid lens ---
    lens = LiquidLens("/dev/optotune_ld")
    lens.to_focal_power_mode()
    print("  Liquid lens ready")

    # --- Open motor stage (automated mode only) ---
    motor: MotorStage | None = None
    if args.motor_port:
        motor = MotorStage(args.motor_port)
        print("  Motor stage ready")

    # --- Prepare CSV ---
    _init_csv(output_path)
    collected: list[tuple[float, float]] = []  # (z, dpt)

    print(f"\nCSV output: {output_path}")
    print("=" * 50)

    try:
        if motor is not None:
            _run_automated(
                args, output_path, cameras_cal, open_ids, basler_cams,
                ximea_cam, ximea_img, lens, roi_slice, motor,
            )
        else:
            while True:
                # 1. Live preview
                confirmed = run_preview_loop(ximea_cam, ximea_img, roi_slice)
                if not confirmed:
                    print("Quitting.")
                    break

                # 2+3. Autofocus + tag detection — retry loop
                while True:
                    best_dpt = run_autofocus(
                        ximea_cam, ximea_img, lens, roi_slice, k=args.sweeps,
                        display_window=PREVIEW_WINDOW,
                    )

                    z = run_tag_detection(
                        basler_cams, open_ids, cameras_cal, args.num_frames, args.tag_family
                    )
                    if z is None:
                        print("No valid Z measured.")
                        ans = (
                            input("R=retry  Q=quit  Enter=skip position: ").strip().lower()
                        )
                        if ans == "q":
                            break
                        if ans == "r":
                            continue
                        break  # skip this position

                    print(f"\nResult: z={z:.4f} m  dpt={best_dpt:.4f}")
                    ans = input("Enter=accept  R=retry  Q=quit: ").strip().lower()
                    if ans == "r":
                        print("Retrying measurement...")
                        continue
                    if ans == "q":
                        break
                    # accepted — record and move on
                    _append_csv(output_path, z, best_dpt)
                    collected.append((z, best_dpt))
                    print(f"Saved ({len(collected)} point(s) total)")
                    break

                if ans == "q":
                    break

                # 4. Continue?
                print("\nMove the AprilTag to the next position.")
                ans = input("Press Enter to continue, or Q to finish: ").strip().lower()
                if ans == "q":
                    break

    except KeyboardInterrupt:
        print("\nInterrupted — proceeding to final plot with collected data.")

    finally:
        ximea_cam.stop_acquisition()
        ximea_cam.close_device()
        for cam in basler_cams:
            cam.Close()
        lens.close()
        if motor is not None:
            motor.close()
        print("Hardware closed.")

    if motor is None and len(collected) >= 1:
        _sort_csv(output_path)
        print(f"CSV sorted by z: {output_path}")

    if motor is None:
        if len(collected) >= 2:
            zs = np.array([p[0] for p in collected])
            dpts = np.array([p[1] for p in collected])
            fit_and_plot(zs, dpts)
        else:
            print(
                f"\nOnly {len(collected)} point(s) collected — need at least 2 for regression."
            )


if __name__ == "__main__":
    main()
