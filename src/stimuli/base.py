"""Base class for all visual stimuli.

Provides abstract interface that all stimulus types must implement.
Designed for novice-friendly extensibility.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any
import pyglet


class BaseStimulus(ABC):
    """Abstract base class for visual stimuli.

    To create a new stimulus:
    1. Inherit from this class
    2. Implement render(), update(), is_active()
    3. Optionally override on_trigger() for closed-loop stimuli
    4. Add config section to configs/config.toml
    5. Register in VisualProcess (src/visual/process.py) for the Panda3D pipeline
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize stimulus from config dictionary.

        Args:
            config: Configuration dictionary from [visual_stimuli.stimulus_name]
        """
        self.config = config

    @abstractmethod
    def render(self, batch: pyglet.graphics.Batch) -> None:
        """Add drawable elements to the pyglet rendering batch.

        Use pyglet.shapes (Circle, Rectangle, etc.) or raw OpenGL.
        All shapes should be added to the provided batch.

        Args:
            batch: Pyglet graphics batch to add drawables to
        """
        pass

    def update(self, dt: float) -> None:
        """Update stimulus state based on elapsed time.

        Called every frame (240 times per second).

        Args:
            dt: Time since last update in seconds (~0.00417s @ 240Hz)
        """
        pass

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Handle TRIGGER message from TriggerHandler.

        Optional for open-loop stimuli (e.g., static patterns).

        Args:
            trigger_data: Dict with keys:
                - obj_id (int): Braid object ID
                - frame (int): Camera frame number
                - braid_timestamp (float): Braid tracking timestamp
                - trigger_timestamp (float): TriggerHandler timestamp
                - mean_heading (float): Fly heading in radians
        """
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """Return True if stimulus should be rendered this frame.

        Returns:
            bool: True to render, False to skip
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up graphics resources (delete shapes, free memory).

        Called when stimulus is no longer needed or process is shutting down.
        """
        pass

    def initialize_rendering(self, batch: pyglet.graphics.Batch) -> None:
        """Optional one-time rendering setup.

        For static stimuli, add shapes to batch here instead of in render().
        For dynamic stimuli, create reusable shape objects here.

        Args:
            batch: Pyglet graphics batch for rendering
        """
        pass
