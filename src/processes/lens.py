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
    """Maps z position to lens diopter via degree-2 polynomial fit."""

    def __init__(self, z_values, dpt_values):
        self.coeffs = np.polyfit(np.array(z_values), np.array(dpt_values), 2)

    def get_dpt(self, z: float) -> float:
        return float(np.polyval(self.coeffs, z))


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
            self.zmq_config.pre_zone_enter_topic,
            self.zmq_config.pre_zone_exit_topic,
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

        if self.lens_config.kalman_enabled:
            self.logger.info(
                f"Kalman filter enabled with process_noise={self.lens_config.process_noise}, "
                f"measurement_noise={self.lens_config.measurement_noise}, "
                f"prediction_horizon={self.lens_config.prediction_horizon}s"
            )
        else:
            self.logger.info("Kalman filter is disabled")

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

    def _stop_tracking(self, reason: str = ""):
        self._flush_timing_csv()
        self.kalman = None
        self.is_tracking = False
        self.current_tracked_obj = None
        if reason:
            self.logger.debug(f"Stopped tracking: {reason}")

    def _run(self):
        self.initialize()
        self.is_running = True
        self.logger.info("Liquid Lens process started.")

        position_timeout = self.lens_config.zone_timeout
        # Both topics treated identically — PRE_ZONE_ENTER just arrives earlier,
        # giving the lens a head-start before the fly reaches the actual trigger zone.
        enter_topics = (
            self.zmq_config.zone_enter_topic,
            self.zmq_config.pre_zone_enter_topic,
        )
        exit_topics = (
            self.zmq_config.zone_exit_topic,
            self.zmq_config.pre_zone_exit_topic,
        )

        while self.is_running and not self.stop_event.is_set():
            try:
                # ----------------------------------------------------------------
                # 1. Check for zone events from TriggerHandler (non-blocking).
                #    These start/stop tracking regardless of whether a Braid
                #    position update is available this iteration.
                # ----------------------------------------------------------------
                try:
                    topic_b, raw = self.trigger_socket.recv_multipart(flags=zmq.NOBLOCK)
                    topic = topic_b.decode()
                    msg = json.loads(raw)

                    # If we're not tracking AND it's an enter_zone topic
                    if topic in enter_topics and not self.is_tracking:
                        obj_id = msg.get("obj_id")
                        if obj_id is not None:
                            event_name = (
                                "PRE_ZONE_ENTER"
                                if topic == self.zmq_config.pre_zone_enter_topic
                                else "ZONE_ENTER"
                            )
                            self.logger.info(
                                f"{event_name}: start tracking object {obj_id}"
                            )

                            # Start tracking this object and log the event.
                            # The actual lens adjustments will begin when the next Braid position update arrives,
                            # which should be shortly since the trigger was just activated.
                            self.is_tracking = True
                            self.current_tracked_obj = obj_id
                            self.last_position_time = time.time()
                            self._log_csv(event_name.lower(), obj_id=obj_id)

                            # Reset per-trial buffers; Kalman starts fresh each trial.
                            self._timing_rows = []
                            self._recording_obj_id = obj_id
                            self._recording_frame = msg.get("frame")
                            self.kalman = None

                    # If we're tracking AND it's an exit_zone topic for the currently tracked object, stop tracking.
                    elif topic in exit_topics and self.is_tracking:
                        if msg.get("obj_id") == self.current_tracked_obj:
                            reason = msg.get("reason", "unknown")
                            event_name = (
                                "PRE_ZONE_EXIT"
                                if topic == self.zmq_config.pre_zone_exit_topic
                                else "ZONE_EXIT"
                            )
                            self.logger.info(
                                f"{event_name}: stop tracking object {self.current_tracked_obj} reason={reason}"
                            )
                            self._log_csv(
                                event_name.lower(),
                                obj_id=self.current_tracked_obj,
                                reason=reason,
                            )
                            self._stop_tracking()

                except zmq.Again:
                    pass  # no trigger message this iteration — normal

                # ----------------------------------------------------------------
                # 2. If not tracking, nothing to do — sleep and loop.
                # ----------------------------------------------------------------
                if not self.is_tracking:
                    time.sleep(0.01)
                    continue

                # ----------------------------------------------------------------
                # 3. Pull the latest position update from Braid (non-blocking).
                #    t_braid_received is stamped here so the per-row delay
                #    reflects end-to-end latency from message arrival to lens cmd.
                # ----------------------------------------------------------------
                t_braid_received = time.time()
                try:
                    _, raw = self.braid_socket.recv_multipart(flags=zmq.NOBLOCK)
                    braid_msg = json.loads(raw)
                except zmq.Again:
                    # No update available. If the gap exceeds position_timeout,
                    # assume the fly left without a clean ZONE_EXIT (e.g. Braid
                    # tracking dropout) and stop tracking defensively.
                    if time.time() - self.last_position_time > position_timeout:
                        self.logger.warning(
                            f"No position data for {position_timeout}s, stopping tracking"
                        )
                        self._log_csv(
                            "timeout",
                            obj_id=self.current_tracked_obj,
                            reason="position_timeout",
                        )
                        self._stop_tracking()
                    else:
                        time.sleep(0.001)
                    continue

                # ----------------------------------------------------------------
                # 4. Handle Braid message types.
                #    Death  → stop tracking immediately (fly lost by Braid).
                #    Update → proceed to focus adjustment below.
                #    Other  → skip (Birth, CalibrationFlydraXml, etc.)
                # ----------------------------------------------------------------
                if "Death" in braid_msg:
                    if braid_msg["Death"] == self.current_tracked_obj:
                        self.logger.info(
                            f"Tracked object {self.current_tracked_obj} died"
                        )
                        self._log_csv(
                            "death",
                            obj_id=self.current_tracked_obj,
                            reason="object_death",
                        )
                        self._stop_tracking()
                    continue

                if "Update" not in braid_msg:
                    continue
                u = braid_msg["Update"]

                # Ignore updates for objects we're not tracking (multi-fly arena).
                if u.get("obj_id") != self.current_tracked_obj:
                    continue

                self.last_position_time = time.time()
                x, y, z = u["x"], u["y"], u["z"]
                vx, vy, vz = u.get("xvel", 0.0), u.get("yvel", 0.0), u.get("zvel", 0.0)
                timestamp = u.get("timestamp")

                # ----------------------------------------------------------------
                # 5. Update Kalman filter with the new measurement.
                #    First update of a trial initialises the filter; subsequent
                #    updates fuse position + velocity into the 6D state estimate.
                # ----------------------------------------------------------------
                if self.lens_config.kalman_enabled:
                    if self.kalman is None:
                        self.kalman = KalmanFilter(
                            process_noise=self.lens_config.process_noise,
                            measurement_noise=self.lens_config.measurement_noise,
                            initial_covariance=self.lens_config.initial_covariance,
                        )
                        self.kalman.init((x, y, z), (vx, vy, vz), timestamp)
                    else:
                        self.kalman.update((x, y, z), (vx, vy, vz), timestamp)

                # ----------------------------------------------------------------
                # 6. Determine the z to focus at.
                #    With Kalman: predict system_latency + prediction_horizon
                #    seconds ahead so the lens is already focused when the fly
                #    arrives, compensating for serial command + mechanical settle.
                #    Without Kalman: use current z directly.
                # ----------------------------------------------------------------
                focus_z = z
                if self.lens_config.kalman_enabled and self.kalman is not None:
                    prediction_time = (
                        self.lens_config.system_latency
                        + self.lens_config.prediction_horizon
                    )
                    predicted = self.kalman.predict(prediction_time)
                    if predicted is not None:
                        focus_z = predicted[2]
                        self.logger.debug(
                            f"Predicted z={focus_z:.3f} (current z={z:.3f})"
                        )

                # ----------------------------------------------------------------
                # 7. Convert z → diopters and send to lens hardware.
                #    Timing is recorded for post-hoc latency analysis.
                # ----------------------------------------------------------------
                try:
                    dpt = self.lens_calibration.get_dpt(focus_z)
                    self.lens_driver.set_diopter(dpt)
                    t_diopter_sent = time.time()
                    delay_ms = (t_diopter_sent - t_braid_received) * 1000.0
                    self.logger.debug(
                        f"Setting lens to {dpt:.3f} dpt for z={focus_z:.3f} (delay={delay_ms:.2f} ms)"
                    )
                    self._log_csv(
                        "focus",
                        obj_id=self.current_tracked_obj,
                        x=x,
                        y=y,
                        z=z,
                        focus_z=focus_z,
                        diopter=dpt,
                        kalman=self.lens_config.kalman_enabled,
                    )
                    self._timing_rows.append(
                        {
                            "t_braid": timestamp,
                            "t_relay": u.get("t_relay"),
                            "t_braid_received": t_braid_received,
                            "t_diopter_sent": t_diopter_sent,
                            "delay_ms": delay_ms,
                            "frame": u.get("frame"),
                            "obj_id": self.current_tracked_obj,
                            "x": x,
                            "y": y,
                            "z": z,
                            "focus_z": focus_z,
                            "diopter": dpt,
                            "kalman": self.lens_config.kalman_enabled,
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
