"""Coordinate conversion utilities for visual stimuli.

Handles mapping between Braid tracking space and display pixel coordinates.
"""

import numpy as np
from typing import Optional
from scipy.interpolate import interp1d


class GeometryUtils:
    """Utility class for coordinate conversions.

    Converts between:
    - Braid heading (radians) → screen pixel x-coordinate
    - Angular size (degrees) → pixel radius
    - Handles calibration mapping and wraparound
    """

    def __init__(
        self,
        screen_width: int = 7680,
        screen_height: int = 1080,
        viewing_distance_cm: float = 25.0,
        screen_width_cm: float = 52.7 * 4,  # 4 screens × 52.7cm
        calibration_file: Optional[str] = None,
        use_empirical_calibration: bool = False,
        heading_offset_deg: float = 0.0
    ):
        """Initialize geometry utilities.

        Args:
            screen_width: Total display width in pixels
            screen_height: Display height in pixels
            viewing_distance_cm: Distance from arena center to screens
            screen_width_cm: Physical width of display in cm
            calibration_file: Path to empirical calibration model (.npz)
            use_empirical_calibration: Use calibration model vs simple mapping
            heading_offset_deg: Fallback angular offset if no calibration
        """
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.viewing_distance_cm = viewing_distance_cm
        self.screen_width_cm = screen_width_cm
        self.heading_offset_deg = heading_offset_deg

        # Calculate pixels per cm
        self.pixels_per_cm = screen_width / screen_width_cm

        # Load calibration if available
        self.interpolator = None
        if use_empirical_calibration and calibration_file:
            try:
                self._load_calibration(calibration_file)
            except FileNotFoundError:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Calibration file not found: {calibration_file}")
                logger.warning("Falling back to simple linear heading→pixel mapping")
                logger.warning("Run: python -m src.processes.visual_stimuli --calibrate-mapping")

    def _load_calibration(self, calibration_file: str) -> None:
        """Load empirical calibration model from file.

        Args:
            calibration_file: Path to .npz file with headings and pixels arrays
        """
        data = np.load(calibration_file)
        headings = data['headings']
        pixels = data['pixels']

        # Create circular interpolator (handles 0°/360° wraparound)
        self.interpolator = self._create_circular_interpolator(headings, pixels)

    def _create_circular_interpolator(
        self,
        headings: np.ndarray,
        pixels: np.ndarray
    ) -> callable:
        """Create interpolator that handles angular wraparound.

        Args:
            headings: Array of heading angles in radians
            pixels: Corresponding pixel x-coordinates

        Returns:
            Interpolator function: heading_rad → pixel_x
        """
        # Sort by heading
        sorted_indices = np.argsort(headings)
        headings_sorted = headings[sorted_indices]
        pixels_sorted = pixels[sorted_indices]

        # Add wraparound points
        headings_extended = np.concatenate([
            headings_sorted[-3:] - 2*np.pi,
            headings_sorted,
            headings_sorted[:3] + 2*np.pi
        ])
        pixels_extended = np.concatenate([
            pixels_sorted[-3:],
            pixels_sorted,
            pixels_sorted[:3]
        ])

        # Create interpolator
        interpolator = interp1d(headings_extended, pixels_extended, kind='linear')

        return lambda h: interpolator(h % (2 * np.pi))

    def heading_to_pixel_x(
        self,
        braid_heading_rad: float,
        stimulus_offset_deg: float
    ) -> int:
        """Convert Braid heading + offset to screen pixel x-coordinate.

        Args:
            braid_heading_rad: Fly heading from Braid (radians)
            stimulus_offset_deg: Angular offset from heading (degrees)

        Returns:
            Pixel x-coordinate (0 to screen_width-1)
        """
        # Convert offset to radians
        offset_rad = np.deg2rad(stimulus_offset_deg)

        # Calculate total heading
        if self.interpolator:
            # Use empirical calibration
            total_heading_rad = braid_heading_rad + offset_rad
            pixel_x = self.interpolator(total_heading_rad)
        else:
            # Fallback: simple linear mapping
            heading_offset_rad = np.deg2rad(self.heading_offset_deg)
            total_heading_rad = braid_heading_rad + offset_rad + heading_offset_rad

            # Normalize to [0, 2π)
            total_heading_rad = total_heading_rad % (2 * np.pi)

            # Convert to pixel x
            pixel_x = (total_heading_rad / (2 * np.pi)) * self.screen_width

        # Wrap to valid range
        return int(pixel_x % self.screen_width)

    def degrees_to_pixels(self, angular_size_deg: float) -> int:
        """Convert angular size to pixel radius.

        Uses small angle approximation: radius ≈ distance × tan(angle)

        Args:
            angular_size_deg: Angular size in degrees

        Returns:
            Radius in pixels
        """
        angular_size_rad = np.deg2rad(angular_size_deg)

        # Calculate physical size at viewing distance
        physical_size_cm = np.tan(angular_size_rad) * self.viewing_distance_cm

        # Convert to pixels
        radius_px = physical_size_cm * self.pixels_per_cm

        return int(radius_px)

    def get_vertical_center(self) -> int:
        """Get vertical center pixel coordinate.

        Returns:
            Y-coordinate for vertical center
        """
        return self.screen_height // 2
