#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pypylon",
#     "opencv-contrib-python",
#     "numpy",
#     "pupil-apriltags",
# ]
# ///
"""
Detect AprilTags across multiple calibrated Basler cameras and triangulate
their 3D positions using a strand-braid calibration XML file.

Usage:
    python apriltag_3d_detect.py --calibration 20260304_115710.xml --num-frames 10

Requirements:
    pip install pypylon opencv-contrib-python numpy
"""

import argparse
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import cv2
import numpy as np
from pypylon import pylon


@dataclass
class CameraCalibration:
    cam_id: str
    # 3x4 projection matrix (projects world coords to pixel coords)
    projection_matrix: np.ndarray
    resolution: tuple[int, int]
    # Intrinsic parameters
    fx: float
    fy: float
    cx: float
    cy: float
    # Distortion coefficients (k1, k2, p1, p2, k3)
    dist_coeffs: np.ndarray
    # Camera matrix
    camera_matrix: np.ndarray


def parse_calibration_xml(xml_path: str) -> dict[str, CameraCalibration]:
    """Parse a strand-braid multi_camera_reconstructor XML calibration file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    cameras = {}
    for single_cal in root.findall("single_camera_calibration"):
        cam_id = single_cal.find("cam_id").text.strip()

        # Parse 3x4 projection matrix (rows separated by ';')
        pmat_text = single_cal.find("calibration_matrix").text.strip()
        rows = pmat_text.split(";")
        projection_matrix = np.array([[float(v) for v in row.split()] for row in rows])

        # Parse resolution
        res_text = single_cal.find("resolution").text.strip().split()
        resolution = (int(res_text[0]), int(res_text[1]))

        # Parse non-linear (distortion) parameters
        nlp = single_cal.find("non_linear_parameters")
        fx = float(nlp.find("fc1").text)
        fy = float(nlp.find("fc2").text)
        cx = float(nlp.find("cc1").text)
        cy = float(nlp.find("cc2").text)
        k1 = float(nlp.find("k1").text)
        k2 = float(nlp.find("k2").text)
        p1 = float(nlp.find("p1").text)
        p2 = float(nlp.find("p2").text)

        camera_matrix = np.array(
            [
                [fx, 0, cx],
                [0, fy, cy],
                [0, 0, 1],
            ]
        )
        dist_coeffs = np.array([k1, k2, p1, p2])

        cameras[cam_id] = CameraCalibration(
            cam_id=cam_id,
            projection_matrix=projection_matrix,
            resolution=resolution,
            fx=fx,
            fy=fy,
            cx=cx,
            cy=cy,
            dist_coeffs=dist_coeffs,
            camera_matrix=camera_matrix,
        )

    return cameras


def capture_images(
    camera_ids: list[str], num_frames: int = 10, debug_dir: str | None = None
) -> dict[str, np.ndarray]:
    """Capture and average num_frames images from each Basler camera."""
    tlf = pylon.TlFactory.GetInstance()
    devices = tlf.EnumerateDevices()

    # Map serial numbers to device info
    device_map = {}
    for dev in devices:
        serial = dev.GetSerialNumber()
        for cam_id in camera_ids:
            # cam_id is like "Basler-40080153", serial is "40080153"
            if cam_id.endswith(serial):
                device_map[cam_id] = dev
                break

    found = set(device_map.keys())
    missing = set(camera_ids) - found
    if missing:
        print(f"Warning: cameras not found: {missing}")
        print(f"Available devices: {[d.GetSerialNumber() for d in devices]}")

    images = {}
    for cam_id, dev_info in device_map.items():
        print(f"Capturing {num_frames} frames from {cam_id}...")
        camera = pylon.InstantCamera(tlf.CreateDevice(dev_info))
        camera.Open()
        camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)

        accumulated = None
        count = 0
        for _ in range(num_frames):
            grab = camera.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            if grab.GrabSucceeded():
                img = grab.Array.astype(np.float64)
                if accumulated is None:
                    accumulated = img
                else:
                    accumulated += img
                count += 1
            grab.Release()

        camera.StopGrabbing()
        camera.Close()

        if count > 0:
            images[cam_id] = (accumulated / count).astype(np.uint8)
            img = images[cam_id]
            print(
                f"  Got {count} frames, shape={img.shape}, "
                f"dtype={img.dtype}, min={img.min()}, max={img.max()}"
            )
            if debug_dir:
                path = os.path.join(debug_dir, f"{cam_id}.png")
                cv2.imwrite(path, img)
                print(f"  Saved debug image: {path}")
        else:
            print(f"  Warning: no frames captured from {cam_id}")

    return images


def detect_apriltags(
    images: dict[str, np.ndarray],
    tag_family: str = "tag36h11",
    debug_dir: str | None = None,
) -> dict[str, list[tuple[int, float, float]]]:
    """
    Detect AprilTags in each image.
    Returns {cam_id: [(tag_id, center_x, center_y), ...]}.
    """
    if tag_family in _ARUCO_FAMILIES:
        return _detect_aruco(images, tag_family, debug_dir=debug_dir)
    if tag_family in _PUPIL_FAMILIES:
        return _detect_pupil(images, tag_family, debug_dir=debug_dir)
    all_families = list(_ARUCO_FAMILIES) + list(_PUPIL_FAMILIES)
    raise ValueError(f"Unknown tag family '{tag_family}'. Choose from: {all_families}")


_ARUCO_FAMILIES = {
    "tag36h11": cv2.aruco.DICT_APRILTAG_36h11,
    "tag25h9": cv2.aruco.DICT_APRILTAG_25h9,
    "tag16h5": cv2.aruco.DICT_APRILTAG_16h5,
}

_PUPIL_FAMILIES = {
    "tagStandard41h12": "tagStandard41h12",
}


def _detect_aruco(
    images: dict[str, np.ndarray],
    tag_family: str,
    debug_dir: str | None = None,
) -> dict[str, list[tuple[int, float, float]]]:
    """Detect tags using OpenCV's ArUco detector with relaxed parameters."""
    params = cv2.aruco.DetectorParameters()
    # Relax adaptive threshold for low-contrast / small tags
    params.adaptiveThreshWinSizeMin = 3
    params.adaptiveThreshWinSizeMax = 53
    params.adaptiveThreshWinSizeStep = 5
    # Lower minimum perimeter so smaller tags are considered
    params.minMarkerPerimeterRate = 0.01
    # Increase tolerance for noisy / slightly curved edges
    params.polygonalApproxAccuracyRate = 0.05
    # Relax corner refinement
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX

    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(_ARUCO_FAMILIES[tag_family]),
        params,
    )

    detections = {}
    for cam_id, img in images.items():
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        corners, ids, rejected = detector.detectMarkers(gray)

        cam_dets = []
        if ids is not None:
            for i, tag_id in enumerate(ids.flatten()):
                center = corners[i][0].mean(axis=0)
                cam_dets.append((int(tag_id), float(center[0]), float(center[1])))

        detections[cam_id] = cam_dets
        _print_detections(cam_id, cam_dets)
        print(f"    rejected candidates: {len(rejected)}")

        # Save debug visualization: detected (green) + rejected (red)
        if debug_dir:
            vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            if ids is not None:
                cv2.aruco.drawDetectedMarkers(vis, corners, ids)
            cv2.aruco.drawDetectedMarkers(vis, rejected, borderColor=(0, 0, 255))
            path = os.path.join(debug_dir, f"{cam_id}_detections.png")
            cv2.imwrite(path, vis)
            print(f"    saved detection debug: {path}")

    return detections


def _detect_pupil(
    images: dict[str, np.ndarray],
    tag_family: str,
    debug_dir: str | None = None,
) -> dict[str, list[tuple[int, float, float]]]:
    """Detect tags using pupil-apriltags (wraps the AprilTag3 C library)."""
    from pupil_apriltags import Detector

    detector = Detector(families=tag_family)

    detections = {}
    for cam_id, img in images.items():
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        results = detector.detect(gray)

        cam_dets = []
        for r in results:
            cx, cy = r.center
            cam_dets.append((int(r.tag_id), float(cx), float(cy)))

        detections[cam_id] = cam_dets
        _print_detections(cam_id, cam_dets)

        if debug_dir:
            vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            for r in results:
                pts = r.corners.astype(int)
                cv2.polylines(vis, [pts], True, (0, 255, 0), 2)
                cx, cy = r.center
                cv2.putText(
                    vis,
                    str(r.tag_id),
                    (int(cx), int(cy)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )
            path = os.path.join(debug_dir, f"{cam_id}_detections.png")
            cv2.imwrite(path, vis)
            print(f"    saved detection debug: {path}")

    return detections


def _print_detections(cam_id: str, cam_dets: list[tuple[int, float, float]]) -> None:
    print(
        f"  {cam_id}: detected {len(cam_dets)} tags"
        + (f" (ids: {[d[0] for d in cam_dets]})" if cam_dets else "")
    )


def undistort_points(points: np.ndarray, cal: CameraCalibration) -> np.ndarray:
    """Undistort 2D pixel coordinates using camera calibration."""
    pts = points.reshape(-1, 1, 2).astype(np.float64)
    undistorted = cv2.undistortPoints(
        pts, cal.camera_matrix, cal.dist_coeffs, P=cal.camera_matrix
    )
    return undistorted.reshape(-1, 2)


def triangulate_points(
    observations: list[tuple[CameraCalibration, np.ndarray]],
) -> np.ndarray:
    """
    Triangulate a 3D point from 2+ camera observations.
    Uses DLT (Direct Linear Transform) via SVD.

    observations: list of (calibration, undistorted_pixel_xy)
    Returns: (x, y, z) in world coordinates.
    """
    # Build the system of equations: for each observation,
    # x * P[2,:] - P[0,:] = 0
    # y * P[2,:] - P[1,:] = 0
    A = []
    for cal, pt in observations:
        P = cal.projection_matrix
        x, y = pt
        A.append(x * P[2, :] - P[0, :])
        A.append(y * P[2, :] - P[1, :])

    A = np.array(A)
    _, _, Vt = np.linalg.svd(A)
    X = Vt[-1]
    return X[:3] / X[3]


def compute_reprojection_error(
    point_3d: np.ndarray,
    observations: list[tuple[CameraCalibration, np.ndarray]],
) -> float:
    """Compute mean reprojection error in pixels."""
    errors = []
    X = np.append(point_3d, 1.0)  # homogeneous
    for cal, observed_pt in observations:
        projected = cal.projection_matrix @ X
        projected_px = projected[:2] / projected[2]
        err = np.linalg.norm(projected_px - observed_pt)
        errors.append(err)
    return float(np.mean(errors))


def main():
    parser = argparse.ArgumentParser(
        description="Detect AprilTags and triangulate their 3D positions"
    )
    parser.add_argument(
        "--calibration", required=True, help="Path to strand-braid XML calibration file"
    )
    parser.add_argument(
        "--num-frames",
        type=int,
        default=10,
        help="Number of frames to average per camera (default: 10)",
    )
    parser.add_argument(
        "--tag-family",
        default="tag36h11",
        choices=["tag36h11", "tag25h9", "tag16h5", "tagStandard41h12"],
        help="AprilTag family (default: tag36h11)",
    )
    parser.add_argument(
        "--images-dir",
        help="Load images from directory instead of capturing. "
        "Files should be named <cam_id>.png (e.g. Basler-40080153.png)",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Save debug images (captured frames, detection overlays with "
        "rejected candidates) to this directory",
    )
    args = parser.parse_args()

    # Create debug directory if requested
    if args.debug_dir:
        os.makedirs(args.debug_dir, exist_ok=True)
        print(f"Debug output: {args.debug_dir}")

    # Parse calibration
    print("Parsing calibration file...")
    cameras = parse_calibration_xml(args.calibration)
    print(f"  Found {len(cameras)} cameras: {list(cameras.keys())}")

    # Capture or load images
    if args.images_dir:
        print(f"\nLoading images from {args.images_dir}...")
        images = {}
        for cam_id in cameras:
            path = os.path.join(args.images_dir, f"{cam_id}.png")
            if os.path.exists(path):
                img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                images[cam_id] = img
                print(
                    f"  Loaded {path}: shape={img.shape}, "
                    f"dtype={img.dtype}, min={img.min()}, max={img.max()}"
                )
            else:
                print(f"  Warning: {path} not found")
    else:
        print(f"\nCapturing {args.num_frames} frames from each camera...")
        images = capture_images(
            list(cameras.keys()), args.num_frames, debug_dir=args.debug_dir
        )

    if not images:
        print("Error: no images available")
        return

    # Detect AprilTags
    print("\nDetecting AprilTags...")
    detections = detect_apriltags(images, args.tag_family, debug_dir=args.debug_dir)

    # Group detections by tag ID across cameras
    tag_observations: dict[int, list[tuple[CameraCalibration, np.ndarray]]] = {}
    for cam_id, cam_dets in detections.items():
        if cam_id not in cameras:
            continue
        cal = cameras[cam_id]
        for tag_id, cx, cy in cam_dets:
            # Undistort the detection center
            undist = undistort_points(np.array([[cx, cy]]), cal)[0]
            tag_observations.setdefault(tag_id, []).append((cal, undist))

    # Triangulate each tag seen by 2+ cameras
    print("\n" + "=" * 60)
    print("3D AprilTag positions")
    print("=" * 60)
    print(
        f"{'Tag ID':>8s} {'X':>10s} {'Y':>10s} {'Z':>10s} {'Reproj (px)':>12s} {'Cameras':>8s}"
    )
    print("-" * 60)

    results = []
    for tag_id in sorted(tag_observations.keys()):
        obs = tag_observations[tag_id]
        if len(obs) < 2:
            print(f"{tag_id:>8d}   (only seen by 1 camera, cannot triangulate)")
            continue

        point_3d = triangulate_points(obs)
        reproj_err = compute_reprojection_error(point_3d, obs)
        results.append((tag_id, point_3d, reproj_err, len(obs)))

        print(
            f"{tag_id:>8d} {point_3d[0]:>10.4f} {point_3d[1]:>10.4f} "
            f"{point_3d[2]:>10.4f} {reproj_err:>12.2f} {len(obs):>8d}"
        )

    if results:
        all_z = [r[1][2] for r in results]
        print("-" * 60)
        print(f"{'Mean Z':>8s} {'':>10s} {'':>10s} {np.mean(all_z):>10.4f}")
        print(f"{'Std Z':>8s} {'':>10s} {'':>10s} {np.std(all_z):>10.4f}")
    else:
        print("\nNo tags seen by 2+ cameras — cannot triangulate.")


if __name__ == "__main__":
    main()
