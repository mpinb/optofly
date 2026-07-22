"""Shared extraction of Braid/TriggerHandler timing fields from a
ZONE_ENTER message.

Used identically by OptoTriggerWorker, VisualProcess, and LiquidLens
instead of three near-duplicate ad-hoc .get() call sites.
"""

from dataclasses import dataclass


@dataclass
class TriggerTiming:
    braid_timestamp: float | None
    handler_timestamp: float | None


def extract_trigger_timing(msg: dict) -> TriggerTiming:
    return TriggerTiming(
        braid_timestamp=msg.get("braid_timestamp"),
        handler_timestamp=msg.get("handler_timestamp"),
    )
