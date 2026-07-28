"""
Latency Logger process — the single writer of latency.csv.

Subscribes to LATENCY messages published by OptoTriggerWorker, VisualProcess,
and LiquidLens (one message per (trigger, system) pair) and writes them to a
single latency.csv in the braid folder. A dedicated process avoids the
header race that three independent processes concurrently appending to the
same file would have on the very first trigger.
"""

import json
import multiprocessing as mp
import os
from typing import Optional

import zmq

from src.utils.config import AppConfig
from src.utils.csv_writer import CSVWriter
from src.utils.worker import WorkerProcess

REQUIRED_KEYS = ("system", "obj_id", "frame", "braid_timestamp", "sham")


class LatencyLogger(WorkerProcess):
    def __init__(
        self,
        event: mp.Event,
        config_path: str = "configs/config.toml",
        braid_folder: Optional[str] = None,
        process_name: str = "LatencyLogger",
        log_level: str = "INFO",
        log_color: str = "BLUE",
        log_path: str | None = None,
    ):
        if event is None:
            raise ValueError("LatencyLogger requires an external stop event.")
        super().__init__(
            event=event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        self.zmq_config = AppConfig.load(config_path).zmq
        self.stop_event = event
        self.braid_folder = braid_folder
        self.csv_writer = None
        self.context = None
        self.latency_socket = None

    def initialize(self) -> None:
        self.context = zmq.Context()
        self.latency_socket = self.context.socket(zmq.PULL)
        bind_address = self.zmq_config.get_publisher_address(
            self.zmq_config.latency_port
        )
        self.latency_socket.bind(bind_address)
        self.logger.debug(f"LATENCY PULL socket bound to {bind_address}")

        csv_path = (
            os.path.join(self.braid_folder, "latency.csv")
            if self.braid_folder
            else "latency.csv"
        )
        self.csv_writer = CSVWriter(csv_path)
        self.logger.info(f"CSV logging to: {csv_path}")

    def _row_from_message(self, message: dict) -> Optional[dict]:
        """Build one latency.csv row from a LATENCY message, or None if the
        message is missing required keys."""
        missing = [key for key in REQUIRED_KEYS if key not in message]
        if missing:
            self.logger.error(
                f"Malformed LATENCY message, missing {missing}: {message}"
            )
            return None

        braid_timestamp = message.get("braid_timestamp")
        activation_timestamp = message.get("activation_timestamp")
        sham = message.get("sham", False)

        latency_ms = None
        if (
            not sham
            and braid_timestamp is not None
            and activation_timestamp is not None
        ):
            latency_ms = (activation_timestamp - braid_timestamp) * 1000.0

        return {
            "obj_id": message.get("obj_id"),
            "frame": message.get("frame"),
            "record_frame": message.get("record_frame"),
            "system": message.get("system"),
            "braid_timestamp": braid_timestamp,
            "trigger_timestamp": message.get("trigger_timestamp"),
            "activation_timestamp": activation_timestamp,
            "latency_ms": latency_ms,
            "sham": sham,
        }

    def _handle_message(self, raw: bytes) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse LATENCY message JSON: {e}")
            return

        row = self._row_from_message(message)
        if row is None:
            return

        try:
            self.csv_writer.append(row)
        except Exception as e:
            self.logger.error(f"Error writing latency.csv row: {e}")

    def _drain(self) -> None:
        while True:
            try:
                raw = self.latency_socket.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            self._handle_message(raw)

    def _run(self) -> None:
        self.initialize()
        self.logger.info("LatencyLogger process started.")

        poller = zmq.Poller()
        poller.register(self.latency_socket, zmq.POLLIN)

        while not self.stop_event.is_set():
            socks = dict(poller.poll(timeout=100))
            if self.latency_socket in socks:
                self._drain()

        # Drain anything still queued before shutting down.
        self._drain()

        self.logger.info("LatencyLogger process stopped.")
        self.close()

    def close(self) -> None:
        if self.csv_writer:
            try:
                self.csv_writer.close()
            except Exception:
                pass
        if self.latency_socket:
            try:
                self.latency_socket.close()
            except Exception:
                pass
        if self.context:
            try:
                self.context.term()
            except Exception:
                pass
        self.logger.info("LatencyLogger process closed.")
