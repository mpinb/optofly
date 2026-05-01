"""BRAID-to-camera coordinate calibration using the Direct Linear Transform (DLT).

Fits a 3×4 projection matrix P from N≥6 correspondences:
    [u, v, 1] ∝ P @ [x, y, z, 1]

Typical use:
    cal = BraidToCameraCalibration()
    cal.fit(world_pts, pixel_pts)
    cal.save("calibrations/braid_to_camera.npz")

    cal = BraidToCameraCalibration.load("calibrations/braid_to_camera.npz")
    u, v = cal.project(x, y, z)
    x, y = cal.backproject(u, v, z)
    fov = cal.compute_fov(width=2112, height=2112, z=0.2)
"""

import numpy as np
from pathlib import Path


class BraidToCameraCalibration:
    """3D→2D projection calibration via DLT."""

    def __init__(self) -> None:
        self._P: np.ndarray | None = None  # 3×4

    @property
    def is_fitted(self) -> bool:
        return self._P is not None

    def fit(
        self,
        world_pts: np.ndarray,
        pixel_pts: np.ndarray,
    ) -> float:
        """Fit projection matrix from correspondences.

        Args:
            world_pts: (N, 3) array of BRAID xyz positions (metres)
            pixel_pts: (N, 2) array of camera pixel (u, v) coordinates

        Returns:
            RMS reprojection error in pixels
        """
        world_pts = np.asarray(world_pts, dtype=float)
        pixel_pts = np.asarray(pixel_pts, dtype=float)
        n = world_pts.shape[0]
        if n < 6:
            raise ValueError(f"Need ≥6 correspondences for DLT, got {n}")

        # Build 2N×12 DLT matrix
        A = np.zeros((2 * n, 12), dtype=float)
        for i in range(n):
            X, Y, Z = world_pts[i]
            u, v = pixel_pts[i]
            A[2 * i] = [X, Y, Z, 1, 0, 0, 0, 0, -u * X, -u * Y, -u * Z, -u]
            A[2 * i + 1] = [0, 0, 0, 0, X, Y, Z, 1, -v * X, -v * Y, -v * Z, -v]

        _, _, Vt = np.linalg.svd(A)
        p = Vt[-1]  # last right singular vector
        self._P = p.reshape(3, 4)

        return self._reprojection_error(world_pts, pixel_pts)

    def project(self, x: float, y: float, z: float) -> tuple[float, float]:
        """Project a BRAID world point to camera pixel coordinates.

        Returns:
            (u, v) pixel coordinates
        """
        if self._P is None:
            raise RuntimeError("Calibration not fitted — call fit() or load() first")
        pt = np.array([x, y, z, 1.0])
        uvw = self._P @ pt
        return float(uvw[0] / uvw[2]), float(uvw[1] / uvw[2])

    def backproject(self, u: float, v: float, z: float) -> tuple[float, float]:
        """Back-project a pixel to BRAID world coordinates at a known z.

        Given pixel (u, v) and known z height, solves for (x, y) by treating the
        projective equation as a 3×3 linear system:

            [P[:,0:2] | -[u,v,1]^T] @ [x, y, w]^T = -(P[:,2]*z + P[:,3])

        Args:
            u: Pixel column coordinate
            v: Pixel row coordinate
            z: Known z height in BRAID world space (metres)

        Returns:
            (x, y) BRAID world coordinates in metres
        """
        if self._P is None:
            raise RuntimeError("Calibration not fitted — call fit() or load() first")
        d = self._P[:, 2] * z + self._P[:, 3]  # (3,) known offset
        A_aug = np.column_stack(
            [self._P[:, 0:2], -np.array([u, v, 1.0])]
        )  # 3×3
        xy_w = np.linalg.solve(A_aug, -d)
        return float(xy_w[0]), float(xy_w[1])

    def compute_fov(
        self, width: int, height: int, z: float
    ) -> dict[str, float]:
        """Compute camera FOV in BRAID world coordinates at a given z height.

        Back-projects the four frame corners to world space to find the axis-aligned
        bounding box.  Use this to fill in [camera.FOV] in config.toml.

        Args:
            width:  Frame width in pixels
            height: Frame height in pixels
            z:      Representative fly height in metres (e.g. midpoint of z_min/z_max)

        Returns:
            dict with keys x_min, x_max, y_min, y_max (metres)
        """
        corners = [
            (0.0, 0.0),
            (float(width), 0.0),
            (float(width), float(height)),
            (0.0, float(height)),
        ]
        xs, ys = [], []
        for u, v in corners:
            x, y = self.backproject(u, v, z)
            xs.append(x)
            ys.append(y)
        return {
            "x_min": float(min(xs)),
            "x_max": float(max(xs)),
            "y_min": float(min(ys)),
            "y_max": float(max(ys)),
        }

    def _reprojection_error(
        self, world_pts: np.ndarray, pixel_pts: np.ndarray
    ) -> float:
        errs = []
        for wp, pp in zip(world_pts, pixel_pts):
            pu, pv = self.project(*wp)
            errs.append((pu - pp[0]) ** 2 + (pv - pp[1]) ** 2)
        return float(np.sqrt(np.mean(errs)))

    def save(self, path: str) -> None:
        if self._P is None:
            raise RuntimeError("Nothing to save — calibration not fitted")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, P=self._P)

    @classmethod
    def load(cls, path: str) -> "BraidToCameraCalibration":
        data = np.load(path)
        cal = cls()
        cal._P = data["P"]
        return cal
