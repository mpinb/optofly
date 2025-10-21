"""Registry for managing active stimulus plugins."""

from typing import Dict, Any
import pyglet
from src.visual_stimuli.base_stimulus import BaseStimulus


class StimulusRegistry:
    """Manages registered stimulus instances.

    Provides centralized dispatch for update, render, and trigger events.
    """

    def __init__(self):
        """Initialize empty registry."""
        self._stimuli: Dict[str, BaseStimulus] = {}

    def register(self, name: str, stimulus: BaseStimulus) -> None:
        """Register a stimulus plugin.

        Args:
            name: Unique identifier for stimulus
            stimulus: BaseStimulus instance
        """
        self._stimuli[name] = stimulus

    def update_all(self, dt: float) -> None:
        """Update all registered stimuli.

        Args:
            dt: Time since last frame in seconds
        """
        for stimulus in self._stimuli.values():
            stimulus.update(dt)

    def render_all(self, batch: pyglet.graphics.Batch) -> None:
        """Render all active stimuli.

        Args:
            batch: Pyglet graphics batch
        """
        for stimulus in self._stimuli.values():
            if stimulus.is_active():
                stimulus.render(batch)

    def on_trigger(self, trigger_data: Dict[str, Any]) -> None:
        """Dispatch TRIGGER message to all stimuli.

        Args:
            trigger_data: Trigger message data
        """
        for stimulus in self._stimuli.values():
            stimulus.on_trigger(trigger_data)

    def get_active_stimuli(self) -> list[str]:
        """Get names of currently active stimuli.

        Returns:
            List of stimulus names that are active
        """
        return [
            name for name, stim in self._stimuli.items()
            if stim.is_active()
        ]
