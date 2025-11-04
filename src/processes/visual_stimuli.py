"""Visual stimuli display process for OptoFly.

Subscribes to TRIGGER messages and renders visual stimuli on 4-screen display.
Supports static patterns and looming stimuli with 240Hz refresh rate.
"""

import argparse
import json
import multiprocessing as mp
import time
from typing import Optional

import pyglet
import zmq

from src.classes.csv_writer import CSVWriter
from src.utils.config import ConfigBase
from src.utils.worker_process import WorkerProcess
from src.visual_stimuli.display_manager import DisplayManager
from src.visual_stimuli.geometry_utils import GeometryUtils
from src.visual_stimuli.static_pattern import StaticPatternStimulus
from src.visual_stimuli.looming_stimulus import LoomingStimulusRenderer
from src.visual_stimuli.stimulus_registry import StimulusRegistry


class VisualStimuliProcess(WorkerProcess):
    """Process for rendering visual stimuli at 240Hz.

    Subscribes to TRIGGER messages via ZMQ and renders registered stimuli
    using pyglet on a 7680×1080 display spanning 4 screens.
    """

    def __init__(
        self,
        config_path: str = "config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "VisualStimuli",
        log_level: str = "INFO",
        log_color: str = "CYAN",
        standalone: bool = False
    ):
        """Initialize VisualStimuliProcess.

        Args:
            config_path: Path to configuration file
            event: Event to signal process termination
            process_name: Name for logging
            log_level: Logging level
            log_color: Color for log messages
            standalone: If True, run in standalone testing mode (no ZMQ, small window)
        """
        # Initialize parent WorkerProcess
        super().__init__(
            event=event,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name
        )

        # Load configuration
        self.config_base = ConfigBase(config_path)._load_config()
        self.config = self.config_base.get("visual_stimuli", {})
        self.stop_event = event if event is not None else mp.Event()

        # Standalone mode flag
        self.standalone = standalone

        # ZMQ connections (None if standalone)
        self.context = None
        self.subscriber = None

        # Display and rendering
        self.display_manager = None
        self.window = None
        self.batch = None

        # Geometry utilities
        self.geometry = None

        # Stimulus registry
        self.registry = StimulusRegistry()

        # CSV logging
        self.csv_writer = None

        # Performance monitoring
        self.frame_times = []
        self.last_performance_log = time.time()

        # Standalone controller (only in standalone mode)
        self.controller = None

        # Initialize logger
        self._initialize_logger()
        mode_str = " (STANDALONE MODE)" if standalone else ""
        self.logger.info(f"Initializing VisualStimuliProcess{mode_str} with config: {config_path}")

    def initialize(self) -> bool:
        """Initialize all components.

        Returns:
            True if initialization successful
        """
        try:
            # Initialize ZMQ (skip in standalone mode)
            if not self.standalone:
                self._initialize_zmq()
            else:
                self.logger.info("Standalone mode: skipping ZMQ initialization")

            # Initialize geometry utilities
            self._initialize_geometry()

            # Initialize CSV logging
            self._initialize_csv()

            # Initialize display
            self._initialize_display()

            # Initialize stimuli
            self._initialize_stimuli()

            # Initialize standalone controller if needed
            if self.standalone:
                self._initialize_standalone_controller()

            self.logger.info("VisualStimuliProcess initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False

    def _initialize_zmq(self) -> None:
        """Initialize ZMQ subscriber to TRIGGER messages."""
        zmq_config = self.config_base.get("zmq", {})
        trigger_port = zmq_config.get("trigger_port", 5556)
        trigger_topic = zmq_config.get("trigger_topic", "TRIGGER")

        self.context = zmq.Context()
        self.subscriber = self.context.socket(zmq.SUB)

        subscriber_address = f"tcp://localhost:{trigger_port}"
        self.logger.info(f"Connecting to TRIGGER messages at {subscriber_address}")
        self.subscriber.connect(subscriber_address)

        # Subscribe to TRIGGER topic
        self.subscriber.setsockopt_string(zmq.SUBSCRIBE, trigger_topic)
        self.logger.info(f"Subscribed to topic: {trigger_topic}")

    def _initialize_geometry(self) -> None:
        """Initialize geometry utilities for coordinate conversion."""
        # Get scale factor for standalone mode
        standalone_config = self.config.get("standalone", {})
        scale_factor = standalone_config.get("scale_factor", 6.0) if self.standalone else 1.0

        self.geometry = GeometryUtils(
            screen_width=self.config.get("window_width", 7680),
            screen_height=self.config.get("window_height", 1080),
            viewing_distance_cm=self.config.get("arena_center_to_screen_cm", 25.0),
            calibration_file=self.config.get("calibration_mapping_file"),
            use_empirical_calibration=self.config.get("use_empirical_calibration", False),
            heading_offset_deg=self.config.get("heading_offset_deg", 0.0),
            scale_factor=scale_factor
        )
        self.logger.info(f"Geometry utilities initialized (scale_factor={scale_factor})")

    def _initialize_csv(self) -> None:
        """Initialize CSV writer for stimulus event logging."""
        log_file = self.config.get("log_file", "visual_stimuli.csv")
        self.csv_writer = CSVWriter(log_file)
        self.logger.info(f"CSV logging to: {log_file}")

    def _initialize_display(self) -> None:
        """Initialize pyglet display window."""
        # Get standalone settings
        standalone_config = self.config.get("standalone", {})

        self.display_manager = DisplayManager(
            window_x_offset=self.config.get("window_x_offset", 3840),
            window_width=self.config.get("window_width", 7680),
            window_height=self.config.get("window_height", 1080),
            background_color=(255, 255, 255, 255),  # White background
            standalone=self.standalone,
            standalone_width=standalone_config.get("window_width", 1280),
            standalone_height=standalone_config.get("window_height", 720)
        )

        self.window = self.display_manager.create_window()
        self.batch = pyglet.graphics.Batch()

        # Set up window event handlers
        @self.window.event
        def on_draw():
            self.window.clear()
            self.batch.draw()

        if self.standalone:
            self.logger.info(
                f"Display window created (standalone): "
                f"{standalone_config.get('window_width', 1280)}×"
                f"{standalone_config.get('window_height', 720)}"
            )
        else:
            self.logger.info(
                f"Display window created: {self.config.get('window_width')}×"
                f"{self.config.get('window_height')} at x={self.config.get('window_x_offset')}"
            )

    def _initialize_stimuli(self) -> None:
        """Initialize and register enabled stimuli."""
        # Register static pattern if enabled
        static_config = self.config.get("static", {})
        if static_config.get("enabled", False):
            static_stimulus = StaticPatternStimulus(static_config)
            self.registry.register("static", static_stimulus)
            self.logger.info("Static pattern stimulus registered")

        # Register looming stimulus if enabled
        looming_config = self.config.get("looming", {})
        if looming_config.get("enabled", False):
            looming_stimulus = LoomingStimulusRenderer(
                config=looming_config,
                geometry_utils=self.geometry,
                logger=self.logger,
                csv_writer=self.csv_writer
            )
            self.registry.register("looming", looming_stimulus)
            self.logger.info("Looming stimulus registered")

        # Initialize rendering after all stimuli registered
        self.registry.initialize_all_rendering(self.batch)
        self.logger.info("Stimulus rendering initialized")

    def _initialize_standalone_controller(self) -> None:
        """Initialize standalone controller for manual testing."""
        from src.visual_stimuli.standalone_controller import StandaloneController

        self.controller = StandaloneController(
            window=self.window,
            registry=self.registry,
            geometry=self.geometry,
            logger=self.logger
        )
        self.logger.info("Standalone controller initialized")

    def _check_trigger_messages(self) -> None:
        """Poll ZMQ for TRIGGER messages (non-blocking)."""
        try:
            # Non-blocking receive
            if self.subscriber.poll(timeout=0):
                topic, message = self.subscriber.recv_multipart(zmq.NOBLOCK)
                message_str = message.decode("utf-8")
                trigger_data = json.loads(message_str)

                # Dispatch to stimuli
                self.registry.on_trigger(trigger_data)

        except zmq.Again:
            pass  # No message available
        except json.JSONDecodeError as e:
            self.logger.error(f"Error decoding TRIGGER message: {e}")
        except Exception as e:
            self.logger.error(f"Error processing TRIGGER message: {e}")

    def _render_loop(self, dt: float) -> None:
        """Main rendering loop called at 240Hz.

        Args:
            dt: Time since last frame (seconds)
        """
        # Record frame time for performance monitoring
        self.frame_times.append(dt)

        # Check for TRIGGER messages (skip in standalone mode)
        if not self.standalone:
            self._check_trigger_messages()

        # Update all stimuli
        self.registry.update_all(dt)

        # Batch persists - stimuli update their shapes in place
        self.registry.render_all(self.batch)

        # Render overlay in standalone mode
        if self.standalone and self.controller:
            self.controller.render_overlay()

        # Log performance every second
        if time.time() - self.last_performance_log >= 1.0:
            self._log_performance()
            self.last_performance_log = time.time()
            self.frame_times = []

    def _log_performance(self) -> None:
        """Log performance metrics."""
        if not self.frame_times:
            return

        import numpy as np
        avg_frame_time = np.mean(self.frame_times)
        max_frame_time = np.max(self.frame_times)

        avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0

        # Warn if performance degraded
        if avg_frame_time > 0.0045:  # > 4.5ms = below 222Hz
            self.logger.warning(
                f"Performance: {avg_fps:.1f} fps "
                f"(avg: {avg_frame_time*1000:.2f}ms, max: {max_frame_time*1000:.2f}ms)"
            )
        else:
            self.logger.debug(
                f"Performance: {avg_fps:.1f} fps "
                f"(avg: {avg_frame_time*1000:.2f}ms, max: {max_frame_time*1000:.2f}ms)"
            )

    def run(self) -> None:
        """Main process loop."""
        if not self.initialize():
            self.logger.error("Failed to initialize, exiting")
            return

        self.logger.info("Starting VisualStimuliProcess")

        # Schedule render loop at 240Hz
        target_fps = self.config.get("target_fps", 240)
        pyglet.clock.schedule_interval(self._render_loop, 1.0 / target_fps)

        # Run pyglet event loop
        pyglet.app.run()

        # Cleanup
        self._cleanup()

    def _cleanup(self) -> None:
        """Clean up resources."""
        self.logger.info("Cleaning up VisualStimuliProcess")

        # Clean up stimulus resources
        if self.registry:
            self.registry.cleanup_all()

        # Close CSV writer
        if self.csv_writer:
            self.csv_writer.close()

        # Close display
        if self.display_manager:
            self.display_manager.close()

        # Close ZMQ
        if self.subscriber:
            self.subscriber.close()
        if self.context:
            self.context.term()

        self.logger.info("Cleanup complete")


# Entry point
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Visual Stimuli Display Process")
    parser.add_argument(
        "--config", "-c",
        default="config.toml",
        help="Path to config file"
    )
    parser.add_argument(
        "--log-level", "-l",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Run screen identification calibration mode"
    )
    parser.add_argument(
        "--calibrate-mapping",
        action="store_true",
        help="Run heading-to-pixel calibration mode"
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Standalone testing mode with manual triggers (no ZMQ, small window)"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode (simulate triggers)"
    )

    args = parser.parse_args()

    # Handle calibration modes
    if args.calibrate:
        from src.visual_stimuli.calibration import run_screen_identification
        run_screen_identification()
        exit(0)

    if args.calibrate_mapping:
        from src.visual_stimuli.calibration import run_heading_calibration
        run_heading_calibration()
        exit(0)

    # TODO: Implement test mode

    # Normal operation or standalone mode
    stop_event = mp.Event()
    process = VisualStimuliProcess(
        config_path=args.config,
        event=stop_event,
        log_level=args.log_level,
        standalone=args.standalone
    )

    try:
        process.run()
    except KeyboardInterrupt:
        print("\nInterrupted, stopping...")
        stop_event.set()
        pyglet.app.exit()
