import csv
import multiprocessing as mp
import os
import statistics
import time
from typing import Callable, Optional

import numpy as np
import zmq
import json

from src.utils.config import AppConfig
from src.utils.csv_writer import CSVWriter
from src.utils.trigger_timing import extract_trigger_timing
from src.utils.worker import WorkerProcess
from optotune_lens import ICC1C as LensDriver


VALID_CALIBRATION_MODELS = ("linear", "quadratic", "power", "inverse")


def _is_lens_rate_limited(
    pending_first_update: Optional[dict], last_cmd_time: float, now_monotonic: float
) -> bool:
    """True if this iteration should be skipped without reading from the
    active-feed socket or running the predictor, because we are still
    inside the lens's 25ms hardware floor.

    Never true for the pending first-update-post-ZONE_ENTER path -- that
    command must always be evaluated. The late rate-limit check right
    before the actual serial write still enforces the floor for it.
    """
    if pending_first_update is not None:
        return False
    return last_cmd_time > 0 and (now_monotonic - last_cmd_time) < 0.025


class LensCalibration:
    """Maps z position (m) -> diopter via a fitted model.

    The model is fit once at construction; get_dpt does only fast
    floating-point arithmetic on the hot path.

    Supported models:
        linear    — dpt = a·z + b
        quadratic — dpt = a·z² + b·z + c  (recommended)
        power     — dpt = a·z^b + c
        inverse   — dpt = a/(z − b) + c   (physically motivated)

    z is clamped to the calibration range to prevent extrapolation.
    """

    def __init__(
        self,
        z_values: np.ndarray,
        dpt_values: np.ndarray,
        model: str = "quadratic",
    ) -> None:
        if model not in VALID_CALIBRATION_MODELS:
            raise ValueError(
                f"calibration_model must be one of {VALID_CALIBRATION_MODELS}, got {model!r}"
            )

        z = np.asarray(z_values, dtype=float)
        dpt = np.asarray(dpt_values, dtype=float)
        self.z_min = float(z.min())
        self.z_max = float(z.max())
        self.model = model
        self._predict: Callable[[float], float]

        if model == "linear":
            _a, _b = np.polyfit(z, dpt, 1)
            a, b = float(_a), float(_b)
            self._predict = lambda z_val, a=a, b=b: a * z_val + b

        elif model == "quadratic":
            _a, _b, _c = np.polyfit(z, dpt, 2)
            a, b, c = float(_a), float(_b), float(_c)
            # Horner's method: fewer multiplies than a*z**2 + b*z + c
            self._predict = lambda z_val, a=a, b=b, c=c: (a * z_val + b) * z_val + c

        elif model == "power":
            from scipy.optimize import curve_fit

            if self.z_min <= 0:
                raise ValueError(
                    "power model requires z_min > 0 (got z_min="
                    f"{self.z_min}); use quadratic or inverse instead."
                )
            popt, _ = curve_fit(
                lambda z, a, b, c: a * z**b + c,
                z,
                dpt,
                p0=[20.0, 1.5, -1.0],
                bounds=([0, 0.1, -np.inf], [np.inf, 5.0, np.inf]),
                maxfev=10_000,
            )
            a, b, c = (float(v) for v in popt)
            self._predict = lambda z_val, a=a, b=b, c=c: a * z_val**b + c

        else:  # inverse: dpt = a / (z - b) + c
            from scipy.optimize import curve_fit

            # The pole b must lie outside the calibration z range.
            # Try both sides (above z_max and below z_min) and keep whichever
            # converges with the lower residual.
            def _inv(z, a, b, c):
                return a / (z - b) + c

            best_popt = None
            best_resid = np.inf
            for p0, bounds in [
                (
                    [-3.0, self.z_max + 0.5, -5.0],
                    ([-np.inf, self.z_max + 1e-3, -np.inf], [np.inf, np.inf, np.inf]),
                ),
                (
                    [3.0, self.z_min - 0.5, 0.0],
                    ([-np.inf, -np.inf, -np.inf], [np.inf, self.z_min - 1e-3, np.inf]),
                ),
            ]:
                try:
                    popt, _ = curve_fit(
                        _inv, z, dpt, p0=p0, bounds=bounds, maxfev=10_000
                    )
                    resid = float(np.sum((dpt - _inv(z, *popt)) ** 2))
                    if resid < best_resid:
                        best_resid = resid
                        best_popt = popt
                except (RuntimeError, ValueError):
                    continue

            if best_popt is None:
                raise RuntimeError("Inverse model fit failed to converge.")

            a, b, c = (float(v) for v in best_popt)
            self._predict = lambda z_val, a=a, b=b, c=c: a / (z_val - b) + c

    def get_dpt(self, z: float) -> float:
        return self._predict(max(self.z_min, min(self.z_max, z)))


def setup_lens_calibration(
    calibration_file: str, model: str = "quadratic"
) -> LensCalibration:
    """Load a (z, dpt) CSV and fit a LensCalibration model.

    Raises:
        RuntimeError: if the file cannot be read or the model fit fails.
    """
    try:
        data = np.genfromtxt(calibration_file, delimiter=",", names=True)
        return LensCalibration(data["z"], data["dpt"], model=model)
    except Exception as e:
        raise RuntimeError(f"Error setting up lens calibration: {e}") from e


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

        app_config = AppConfig.load(config_path)
        self.lens_config = app_config.liquid_lens
        self.zmq_config = app_config.zmq
        self.camera_config = app_config.camera
        self.stop_event = event
        self.is_running = False
        self.is_tracking = False
        self.current_tracked_obj = None
        self._trial_count = 0
        self.braid_folder = braid_folder
        self.video_folder = video_folder
        self.csv_writer = None

        # Last diopter actually commanded to the lens, for slew-rate limiting.
        # Persists across trials so the onset jump is ramped from the lens's
        # real resting position. None until the first command is sent.
        self._last_dpt: Optional[float] = None
        # Monotonic timestamp of the last serial command; enforces the lens's
        # 25 ms minimum inter-command interval (~40 Hz max update rate).
        self._last_cmd_time: float = 0.0

        self._timing_rows: list = []
        self._recording_obj_id: Optional[int] = None
        self._recording_frame: Optional[int] = None
        self._pending_first_update: Optional[dict] = None

        # Consecutive-duplicate suppression for the focus loop's error paths;
        # see _log_error_throttled().
        self._last_error_message: Optional[str] = None
        self._suppressed_error_count: int = 0

    def initialize(self):
        self.logger.debug(f"Liquid Lens config: {self.lens_config}")

        # ZMQ
        self.context = zmq.Context()
        self.active_braid_socket = self.context.socket(zmq.SUB)
        if self.zmq_config.lens_update_conflate:
            self.active_braid_socket.setsockopt(zmq.CONFLATE, 1)
            self.active_braid_socket.setsockopt(zmq.RCVHWM, 1)
        self.active_braid_socket.connect(
            self.zmq_config.get_subscriber_address(self.zmq_config.active_braid_port)
        )
        self.active_braid_socket.setsockopt_string(
            zmq.SUBSCRIBE, self.zmq_config.active_braid_topic
        )

        self.trigger_socket = self.context.socket(zmq.SUB)
        self.trigger_socket.connect(
            self.zmq_config.get_subscriber_address(self.zmq_config.trigger_port)
        )
        for topic in (
            self.zmq_config.zone_enter_topic,
            self.zmq_config.zone_exit_topic,
        ):
            self.trigger_socket.setsockopt_string(zmq.SUBSCRIBE, topic)
        self.logger.debug("Connected to BraidPublisher active feed and TriggerHandler.")

        # LATENCY reporting: PUSH connects to LatencyLogger's bound PULL socket.
        self.latency_socket = self.context.socket(zmq.PUSH)
        # Without these, a dead/slow LatencyLogger fills the default
        # SNDHWM=1000 queue and the next .send() blocks forever, freezing
        # this process's lens-focusing loop while is_alive() still reports
        # it as running. SNDTIMEO=0 makes .send() raise zmq.Again instead
        # (already caught below); LINGER=0 keeps context.term() from
        # hanging on shutdown to flush a backlog.
        self.latency_socket.setsockopt(zmq.SNDTIMEO, 0)
        self.latency_socket.setsockopt(zmq.LINGER, 0)
        self.latency_socket.connect(
            self.zmq_config.get_subscriber_address(self.zmq_config.latency_port)
        )

        # Calibration
        self.lens_calibration = setup_lens_calibration(
            self.lens_config.calibration_file,
            model=self.lens_config.calibration_model,
        )
        self.logger.debug(
            f"Lens calibration loaded: model={self.lens_config.calibration_model}"
        )

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
        if mode == "linear":
            self.logger.info(
                f"Predictor: linear (system_latency={self.lens_config.system_latency}s, "
                f"prediction_horizon={self.lens_config.prediction_horizon}s)"
            )
        else:
            self.logger.info("Predictor: none (raw z from Braid)")

        self._prediction_time = (
            self.lens_config.system_latency + self.lens_config.prediction_horizon
        )

        self.logger.info("Liquid Lens process initialized.")

    def _log_error_throttled(self, message: str) -> None:
        """Log a per-iteration error without flooding the log.

        The focus loop polls on a 10 ms timeout, so a fault that doesn't clear
        (unplugged serial, wedged firmware) would otherwise write one line per
        iteration into optofly.log for the rest of the run -- burying every
        other diagnostic and competing for the disk the recordings go to.

        Identical consecutive messages are counted rather than repeated; the
        count is flushed as soon as the message changes, so a changing fault
        is never hidden.
        """
        if message == self._last_error_message:
            self._suppressed_error_count += 1
            return
        self._flush_suppressed_errors()
        self._last_error_message = message
        self.logger.error(message)

    def _flush_suppressed_errors(self) -> None:
        """Emit the tally for a run of suppressed identical errors, if any."""
        if self._suppressed_error_count:
            self.logger.error(
                "(previous lens error repeated %d more time(s))",
                self._suppressed_error_count,
            )
            self._suppressed_error_count = 0

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
            self.logger.debug(
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
        self.is_tracking = False
        self.current_tracked_obj = None
        self._pending_first_update = None

    def _drain_active_braid_idle(self):
        """Discard queued active-object updates while no trial is active."""
        while True:
            try:
                self.active_braid_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break

    def _get_latest_active_update(self):
        """Drain active-object updates and return the newest payload for the
        object this LiquidLens instance is currently tracking.

        BraidPublisher broadcasts whichever object most recently triggered
        ZONE_ENTER, but LiquidLens ignores ZONE_ENTER while a trial is
        already active. If a second object triggers before the first
        trial ends, the two processes would otherwise disagree about
        which object is "active" -- filtering by obj_id here keeps
        current_tracked_obj authoritative.
        """
        latest = None

        while True:
            try:
                _, raw = self.active_braid_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            payload = json.loads(raw)
            if payload.get("obj_id") == self.current_tracked_obj:
                latest = payload

        return latest

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
                    self._trial_count += 1
                    self.logger.info(
                        "[#%d obj=%d] start tracking",
                        self._trial_count,
                        obj_id,
                    )
                    self.is_tracking = True
                    self.current_tracked_obj = obj_id
                    self._log_csv("zone_enter", obj_id=obj_id)
                    self._timing_rows = []
                    self._recording_obj_id = obj_id
                    self._recording_frame = msg.get("frame")
                    self._pending_first_update = {
                        key: msg[key]
                        for key in (
                            "obj_id",
                            "frame",
                            "record_frame",
                            "x",
                            "y",
                            "z",
                            "xvel",
                            "yvel",
                            "zvel",
                            "timestamp",
                            "t_relay",
                            "braid_timestamp",
                            "handler_timestamp",
                        )
                        if key in msg
                    }

            elif topic == self.zmq_config.zone_exit_topic and self.is_tracking:
                if msg.get("obj_id") == self.current_tracked_obj:
                    reason = msg.get("reason", "unknown")
                    self.logger.info(
                        "[#%d obj=%d] stop tracking (reason=%s)",
                        self._trial_count,
                        self.current_tracked_obj,
                        reason,
                    )
                    self._log_csv(
                        "zone_exit",
                        obj_id=self.current_tracked_obj,
                        reason=reason,
                    )
                    self._stop_tracking()

    def _publish_latency(self, update: dict, activation_timestamp: float) -> None:
        """Publish one LATENCY message for the first post-ZONE_ENTER
        command only -- lens is never sham (it always focuses while
        tracking)."""
        try:
            timing = extract_trigger_timing(update)
            message = {
                "system": "lens",
                "obj_id": update.get("obj_id"),
                "frame": update.get("frame"),
                "record_frame": update.get("record_frame"),
                "braid_timestamp": timing.braid_timestamp,
                "trigger_timestamp": timing.handler_timestamp,
                "activation_timestamp": activation_timestamp,
                "sham": False,
            }
            self.latency_socket.send(json.dumps(message).encode("utf-8"))
        except Exception as e:
            self.logger.error(f"Error publishing LATENCY message: {e}")

    def _run(self):
        self.initialize()
        self.is_running = True
        self.logger.info("Liquid Lens process started.")

        poller = zmq.Poller()
        poller.register(self.trigger_socket, zmq.POLLIN)
        poller.register(self.active_braid_socket, zmq.POLLIN)

        while self.is_running and not self.stop_event.is_set():
            try:
                socks = {s for s, _ in poller.poll(timeout=10)}

                if self.trigger_socket in socks:
                    self._drain_trigger_socket()

                if not self.is_tracking:
                    if self.active_braid_socket in socks:
                        self._drain_active_braid_idle()
                    continue

                # Rate-limit before doing any predictor work: the linear
                # predictor is stateless (no filter state to keep "warm"
                # between commands), so a measurement received while still
                # inside the hardware's 25ms floor will simply be
                # superseded by BraidPublisher's next update ~10ms later --
                # nothing is lost by skipping it early. Never applies to
                # the pending first-update-post-ZONE_ENTER command, which
                # must always be processed; the late check further below
                # still enforces the floor for that path.
                if _is_lens_rate_limited(
                    self._pending_first_update, self._last_cmd_time, time.monotonic()
                ):
                    continue

                if self._pending_first_update is not None:
                    is_first_command = True
                    update = self._pending_first_update
                    self._pending_first_update = None
                elif self.active_braid_socket in socks:
                    is_first_command = False
                    update = self._get_latest_active_update()
                else:
                    continue

                if update is None:
                    continue

                t_lens_recv = time.time()
                x, y, z = update["x"], update["y"], update["z"]
                vz = update.get("zvel", 0.0)
                timestamp = update.get("timestamp")
                t_relay = update.get("t_relay")

                # Pick the focus depth according to predictor mode.
                predictor = self.lens_config.predictor
                if predictor == "linear":
                    focus_z = z + vz * self._prediction_time
                else:
                    focus_z = z

                # Command the lens and record timing.
                prev_dpt = self._last_dpt
                try:
                    target_dpt = self.lens_calibration.get_dpt(focus_z)
                    # Slew-rate limit: ramp large transitions so the lens's
                    # ~400 Hz resonance isn't excited by abrupt steps.
                    max_step = self.lens_config.max_diopter_step
                    if max_step > 0 and self._last_dpt is not None:
                        delta = target_dpt - self._last_dpt
                        delta = max(-max_step, min(max_step, delta))
                        dpt = self._last_dpt + delta
                    else:
                        dpt = target_dpt
                    if prev_dpt is not None and abs(dpt - prev_dpt) < 1e-5:
                        continue
                    now = time.monotonic()
                    if now - self._last_cmd_time < 0.025:
                        continue
                    self._last_dpt = dpt
                    self._last_cmd_time = now
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
                            "t_lens_recv": t_lens_recv,
                            "t_serial_start": t_serial_start,
                            "t_diopter_sent": t_diopter_sent,
                            "delay_ms": delay_ms,
                            "frame": update.get("frame"),
                            "obj_id": self.current_tracked_obj,
                            "x": x,
                            "y": y,
                            "z": z,
                            "focus_z": focus_z,
                            "diopter": dpt,
                            "target_diopter": target_dpt,
                            "predictor": self.lens_config.predictor,
                        }
                    )
                    if is_first_command:
                        self._publish_latency(update, t_diopter_sent)
                except Exception as e:
                    self._log_error_throttled(f"Error adjusting lens: {e}")

            except Exception as e:
                self._log_error_throttled(f"Error in Liquid Lens process: {e}")

        self._flush_suppressed_errors()
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
        for sock_attr in ("active_braid_socket", "trigger_socket", "latency_socket"):
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
