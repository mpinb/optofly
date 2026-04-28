import csv
import multiprocessing as mp
import os
import statistics
import time
from typing import Optional

import numpy as np
import zmq
import json

from src.utils.config import LiquidLensConfig, ZMQConfig, CameraConfig
from src.utils.csv_writer import CSVWriter
from src.utils.worker import WorkerProcess
from src.utils.kalman_filter import KalmanFilter
from src.hardware.lens import LensDriver


class LensCalibration:
    """Maps z position to lens diopter via linear model (A*z + B).

    Coefficients are fit once at construction from calibration data.
    z is clamped to the calibration range to prevent extrapolation.
    """

    def __init__(self, z_values, dpt_values):
        z = np.array(z_values)
        dpt = np.array(dpt_values)
        self.a, self.b = np.polyfit(z, dpt, 1)
        self.z_min = float(z.min())
        self.z_max = float(z.max())

    def get_dpt(self, z: float) -> float:
        z = max(self.z_min, min(self.z_max, z))
        return self.a * z + self.b


def setup_lens_calibration(calibration_file: str) -> LensCalibration:
    try:
        data = np.genfromtxt(calibration_file, delimiter=",", names=True)
        return LensCalibration(data["z"], data["dpt"])
    except Exception as e:
        raise RuntimeError(f"Error setting up lens calibration: {e}")


class LiquidLens(WorkerProcess):
    def __init__(
        self,
        event: mp.Event,
        config_path: str = "configs/config.toml",
        braid_folder: Optional[str] = None,
        video_folder: Optional[str] = None,
        process_name: str = "LiquidLens",
        log_level: str = "INFO",
        log_color: str = "GREEN",
        log_path: str | None = None,
    ):
        if event is None:
            raise ValueError("LiquidLens requires an external stop event.")
        super().__init__(
            event=event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        self.lens_config = LiquidLensConfig(config_path)
        self.zmq_config = ZMQConfig(config_path)
        self.camera_config = CameraConfig(config_path)
        self.stop_event = event
        self.is_running = False
        self.is_tracking = False
        self.current_tracked_obj = None
        self.braid_folder = braid_folder
        self.video_folder = video_folder
        self.csv_writer = None
        self.kalman: Optional[KalmanFilter] = None

        self._timing_rows: list = []
        self._recording_obj_id: Optional[int] = None
        self._recording_frame: Optional[int] = None

    def initialize(self):
        self.logger.debug(f"Liquid Lens config: {self.lens_config}")

        # ZMQ
        self.context = zmq.Context()
        self.braid_socket = self.context.socket(zmq.SUB)
        self.braid_socket.connect(
            self.zmq_config.get_subscriber_address(self.zmq_config.braid_port)
        )
        self.braid_socket.setsockopt_string(zmq.SUBSCRIBE, self.zmq_config.braid_topic)

        self.trigger_socket = self.context.socket(zmq.SUB)
        self.trigger_socket.connect(
            self.zmq_config.get_subscriber_address(self.zmq_config.trigger_port)
        )
        for topic in (
            self.zmq_config.zone_enter_topic,
            self.zmq_config.zone_exit_topic,
        ):
            self.trigger_socket.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.logger.debug("Connected to BraidPublisher and TriggerHandler.")

        # Calibration
        self.lens_calibration = setup_lens_calibration(
            self.lens_config.calibration_file
        )
        self.logger.debug("Lens calibration loaded.")

        # Lens hardware
        self.lens_driver = LensDriver(port=self.lens_config.port)
        if self.lens_config.mode == "diopter":
            self.lens_driver.to_focal_power_mode()
        elif self.lens_config.mode == "current":
            self.lens_driver.to_current_mode()
        else:
            raise ValueError(f"Invalid lens mode: {self.lens_config.mode}")

        # CSV writer
        csv_path = (
            os.path.join(self.braid_folder, "liquid_lens.csv")
            if self.braid_folder
            else "liquid_lens.csv"
        )
        self.csv_writer = CSVWriter(csv_path, strict=False)
        self.logger.info(f"CSV logging to: {csv_path}")

        mode = self.lens_config.predictor
        if mode == "kalman":
            self.logger.info(
                f"Predictor: kalman (process_noise={self.lens_config.process_noise}, "
                f"measurement_noise={self.lens_config.measurement_noise}, "
                f"system_latency={self.lens_config.system_latency}s, "
                f"prediction_horizon={self.lens_config.prediction_horizon}s)"
            )
        elif mode == "linear":
            self.logger.info(
                f"Predictor: linear (system_latency={self.lens_config.system_latency}s, "
                f"prediction_horizon={self.lens_config.prediction_horizon}s)"
            )
        else:
            self.logger.info("Predictor: none (raw z from Braid)")

        self.logger.info("Liquid Lens process initialized.")

    def _log_csv(self, event: str, **kwargs):
        if self.csv_writer is None:
            return
        row = {"timestamp": time.time(), "event": event}
        row.update(kwargs)
        self.csv_writer.append(row)

    def _flush_timing_csv(self):
        if not self._timing_rows or self._recording_obj_id is None:
            return
        save_folder = self.video_folder or self.camera_config.save_folder
        fname = f"obj_id_{self._recording_obj_id}_frame_{self._recording_frame}_lens_timing.csv"
        csv_path = os.path.join(save_folder, fname)
        try:
            os.makedirs(save_folder, exist_ok=True)
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(self._timing_rows[0].keys()))
                writer.writeheader()
                writer.writerows(self._timing_rows)
            delays = [r["delay_ms"] for r in self._timing_rows]
            self.logger.info(
                f"Lens timing CSV written: {csv_path} ({len(delays)} rows, "
                f"mean delay={statistics.mean(delays):.2f} ms, "
                f"max delay={max(delays):.2f} ms)"
            )
        except Exception as e:
            self.logger.error(f"Error writing lens timing CSV {csv_path}: {e}")
        finally:
            self._timing_rows = []
            self._recording_obj_id = None
            self._recording_frame = None

    def _stop_tracking(self):
        self._flush_timing_csv()
        self.kalman = None
        self.is_tracking = False
        self.current_tracked_obj = None

    def _drain_braid_idle(self):
        """Discard queued BRAID traffic while no trial is active."""
        while True:
            try:
                self.braid_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

    def _get_next_update_for_current_object(self):
        """Drain the queue and return the most recent BRAID update for the tracked object.

        Older queued updates are discarded — the Kalman predictor extrapolates
        from the freshest measurement, so chasing stale positions is wasted work.
        """
        latest = None
        saw_death = False

        while True:
            try:
                _, raw = self.braid_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

            braid_msg = json.loads(raw)

            if "Death" in braid_msg and braid_msg["Death"] == self.current_tracked_obj:
                saw_death = True
                continue

            if "Update" not in braid_msg:
                continue

            update = braid_msg["Update"]
            if update.get("obj_id") != self.current_tracked_obj:
                continue

            latest = update

        return latest, saw_death

    def _drain_trigger_socket(self):
        """Process all pending zone enter/exit events."""
        while True:
            try:
                topic_b, raw = self.trigger_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            topic = topic_b.decode()
            msg = json.loads(raw)

            if topic == self.zmq_config.zone_enter_topic and not self.is_tracking:
                obj_id = msg.get("obj_id")
                if obj_id is not None:
                    self.logger.info(f"ZONE_ENTER: start tracking object {obj_id}")
                    self.is_tracking = True
                    self.current_tracked_obj = obj_id
                    self.last_position_time = time.time()
                    self._log_csv("zone_enter", obj_id=obj_id)
                    self._timing_rows = []
                    self._recording_obj_id = obj_id
                    self._recording_frame = msg.get("frame")
                    self.kalman = None

            elif topic == self.zmq_config.zone_exit_topic and self.is_tracking:
                if msg.get("obj_id") == self.current_tracked_obj:
                    reason = msg.get("reason", "unknown")
                    self.logger.info(
                        f"ZONE_EXIT: stop tracking object {self.current_tracked_obj} reason={reason}"
                    )
                    self._log_csv(
                        "zone_exit",
                        obj_id=self.current_tracked_obj,
                        reason=reason,
                    )
                    self._stop_tracking()

    def _run(self):
        self.initialize()
        self.is_running = True
        self.logger.info("Liquid Lens process started.")

        poller = zmq.Poller()
        poller.register(self.trigger_socket, zmq.POLLIN)
        poller.register(self.braid_socket, zmq.POLLIN)

        while self.is_running and not self.stop_event.is_set():
            try:
                events = dict(poller.poll(timeout=10))

                if self.trigger_socket in events:
                    self._drain_trigger_socket()

                if not self.is_tracking:
                    if self.braid_socket in events:
                        self._drain_braid_idle()
                    continue

                if self.braid_socket not in events:
                    continue

                update, saw_death = self._get_next_update_for_current_object()

                if saw_death:
                    self.logger.warning(
                        "BRAID reported death for tracked object %s before ZONE_EXIT",
                        self.current_tracked_obj,
                    )

                if update is None:
                    continue

                u = update
                self.last_position_time = time.time()
                x, y, z = u["x"], u["y"], u["z"]
                vx, vy, vz = u.get("xvel", 0.0), u.get("yvel", 0.0), u.get("zvel", 0.0)
                timestamp = u.get("timestamp")
                t_relay = u.get("t_relay")

                # Pick the focus depth according to predictor mode.
                predictor = self.lens_config.predictor
                if predictor == "kalman":
                    if self.kalman is None:
                        self.kalman = KalmanFilter(
                            process_noise=self.lens_config.process_noise,
                            measurement_noise=self.lens_config.measurement_noise,
                            initial_covariance=self.lens_config.initial_covariance,
                            velocity_noise=self.lens_config.velocity_noise,
                        )
                        self.kalman.init((x, y, z), (vx, vy, vz), timestamp)
                    else:
                        self.kalman.update((x, y, z), (vx, vy, vz), timestamp)
                    prediction_time = (
                        self.lens_config.system_latency
                        + self.lens_config.prediction_horizon
                    )
                    predicted = self.kalman.predict(prediction_time)
                    focus_z = predicted[2] if predicted is not None else z
                elif predictor == "linear":
                    prediction_time = (
                        self.lens_config.system_latency
                        + self.lens_config.prediction_horizon
                    )
                    focus_z = z + vz * prediction_time
                else:
                    focus_z = z

                # Command the lens and record timing.
                try:
                    dpt = self.lens_calibration.get_dpt(focus_z)
                    t_serial_start = time.time()
                    self.lens_driver.set_diopter(dpt)
                    t_diopter_sent = time.time()
                    delay_ms = (t_diopter_sent - t_serial_start) * 1000.0
                    self._log_csv(
                        "focus",
                        obj_id=self.current_tracked_obj,
                        x=x,
                        y=y,
                        z=z,
                        focus_z=focus_z,
                        diopter=dpt,
                        predictor=self.lens_config.predictor,
                    )
                    self._timing_rows.append(
                        {
                            "t_braid": timestamp,
                            "t_relay": t_relay,
                            "t_serial_start": t_serial_start,
                            "t_diopter_sent": t_diopter_sent,
                            "delay_ms": delay_ms,
                            "frame": u.get("frame"),
                            "obj_id": self.current_tracked_obj,
                            "x": x,
                            "y": y,
                            "z": z,
                            "focus_z": focus_z,
                            "diopter": dpt,
                            "predictor": self.lens_config.predictor,
                        }
                    )
                except Exception as e:
                    self.logger.error(f"Error adjusting lens: {e}")

            except Exception as e:
                self.logger.error(f"Error in Liquid Lens process: {e}")

        self.logger.info("Liquid Lens process stopped.")
        self.close()

    def close(self):
        self.is_running = False
        self.is_tracking = False
        self._flush_timing_csv()
        if self.csv_writer:
            try:
                self.csv_writer.close()
            except Exception:
                pass
        if hasattr(self, "lens_driver"):
            try:
                self.lens_driver.close()
            except Exception:
                pass
        for sock_attr in ("braid_socket", "trigger_socket"):
            sock = getattr(self, sock_attr, None)
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        if hasattr(self, "context") and self.context:
            try:
                self.context.term()
            except Exception:
                pass
        self.logger.info("Liquid Lens process closed.")
