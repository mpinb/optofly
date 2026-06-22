"""
Braid Server subscriber that connects to a Braid server and publishes events to ZMQ.
"""

import json
import logging
import multiprocessing as mp
import signal
import time
from threading import Thread
from typing import Any, Dict, Iterable, Iterator, Optional

import requests
import zmq
from requests.adapters import HTTPAdapter

from src.utils.config import BraidPublisherConfig
from src.utils.logger import configure_process_logging
from src.utils.worker import WorkerProcess

# Constants
DATA_PREFIX = "data: "
MAX_RETRIES = 5
RETRY_DELAY = 2
# Warn if the wall-clock gap between SSE event boundaries exceeds this.
# Braid runs at 100 Hz (10 ms cadence), so >25 ms means an upstream stall.
BOUNDARY_GAP_WARN_S = 0.025

def iter_sse_events(lines: Iterable[str]) -> Iterator[tuple[Optional[str], str]]:
    """Yield complete SSE events from an iterable of decoded lines."""
    event_type: Optional[str] = None
    data_buf: list[str] = []

    def flush_event():
        nonlocal event_type, data_buf
        if data_buf:
            yield event_type, "\n".join(data_buf)
        event_type = None
        data_buf = []

    for line in lines:
        if line is None:
            continue

        if line == "":
            yield from flush_event()
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = line[len("data:") :]
            if payload.startswith(" "):
                payload = payload[1:]
            data_buf.append(payload)

    yield from flush_event()


class BraidPublisher(WorkerProcess):
    """
    Process that subscribes to a Braid server and publishes events to ZMQ.

    This class establishes a connection to a Braid server's event stream and
    forwards messages to a ZMQ PUB socket for other processes to consume.
    """

    def __init__(
        self,
        config_path: str = "configs/config.toml",
        event: Optional[mp.Event] = None,
        process_name: str = "BraidPublisher",
        log_level: str = "INFO",
        log_color: str = "GREEN",
        log_path: str | None = None,
    ):
        """
        Initialize the BraidPublisher.

        Args:
            config_path: Path to the configuration file
            event: Event to signal process termination (created if None)
            log_level: Logging level to use
            log_color: Color for log messages
            process_name: Name to display in logs
            log_path: Path to shared log file (written from child process)
        """
        # Pass parameters to parent class
        super().__init__(
            event=event,
            log_path=log_path,
            log_level=log_level,
            log_color=log_color,
            process_name=process_name,
        )

        # Initialize our specific attributes
        self.config = BraidPublisherConfig(config_path)
        self.stop_event = event if event is not None else mp.Event()

        # Pre-encode constants used on the per-event hot path.
        self._topic_bytes = self.config.zmq.braid_topic.encode("utf-8")
        self._active_topic_bytes = self.config.zmq.active_braid_topic.encode("utf-8")
        self._active_obj_id: Optional[int] = None
        self._active_last_seen: float = 0.0  # monotonic time of last matching Update

        # Connection objects (initialized later)
        self.session = None
        self.events_url = None
        self.zmq_context = None
        self.zmq_socket = None
        self.active_braid_socket = None
        self.trigger_socket = None
        self.stream_thread = None
        self.is_connected = False

    def _handle_signal(self, signum, frame):
        """Handle termination signals by setting the stop event."""
        self.logger.debug(f"Received signal {signum}, shutting down...")
        self.stop_event.set()

    def initialize(self) -> bool:
        """
        Initialize connections to Braid server and ZMQ.

        Returns:
            True if both connections were established successfully, False otherwise
        """
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        try:
            self._connect_to_braid()
            self._connect_to_zmq()
            self.is_connected = True
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize BraidSubscriber: {e}")
            self.close()
            return False

    def _connect_to_braid(self) -> None:
        """
        Connect to the Braid server's event stream.

        Raises:
            Exception: If connection fails
        """
        self.logger.debug(f"Connecting to Braid server at {self.config.url}")

        self.session = requests.Session()
        # Reconnect policy is owned by our outer loop in `_process_stream`;
        # disable urllib3's hidden retry path so failures surface immediately.
        no_retry_adapter = HTTPAdapter(max_retries=0)
        self.session.mount("http://", no_retry_adapter)
        self.session.mount("https://", no_retry_adapter)

        # Test the connection
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(self.config.url, timeout=self.config.timeout)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    self.logger.debug(
                        f"Connection attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    raise Exception(
                        f"Failed to connect to {self.config.url} after {MAX_RETRIES} attempts: {e}"
                    )

        # Prepare events URL
        self.events_url = f"{self.config.url.rstrip('/')}/events"
        self.logger.info(
            f"Successfully connected to Braid server. Event stream at {self.events_url}"
        )

    def _connect_to_zmq(self) -> None:
        """
        Set up the ZMQ publisher socket.

        Raises:
            Exception: If ZMQ connection fails
        """
        try:
            # Initialize ZMQ context and socket
            self.zmq_context = zmq.Context()
            self.zmq_socket = self.zmq_context.socket(zmq.PUB)
            self.zmq_socket.setsockopt(zmq.SNDHWM, self.config.zmq.braid_pub_hwm)
            self.zmq_socket.setsockopt(zmq.TCP_NODELAY, 1)
            bind_address = self.config.zmq.get_publisher_address(
                self.config.zmq.braid_port
            )

            self.logger.debug(f"Binding ZMQ publisher to {bind_address}")
            self.zmq_socket.bind(bind_address)

            self.active_braid_socket = self.zmq_context.socket(zmq.PUB)
            self.active_braid_socket.setsockopt(zmq.SNDHWM, 1)
            self.active_braid_socket.setsockopt(zmq.TCP_NODELAY, 1)
            active_bind_address = self.config.zmq.get_publisher_address(
                self.config.zmq.active_braid_port
            )
            self.logger.debug(f"Binding active BRAID publisher to {active_bind_address}")
            self.active_braid_socket.bind(active_bind_address)

            self.trigger_socket = self.zmq_context.socket(zmq.SUB)
            self.trigger_socket.setsockopt(zmq.RCVHWM, 100)
            self.trigger_socket.setsockopt(zmq.TCP_NODELAY, 1)
            trigger_address = self.config.zmq.get_subscriber_address(
                self.config.zmq.trigger_port
            )
            self.logger.debug(f"Connecting trigger subscriber to {trigger_address}")
            self.trigger_socket.connect(trigger_address)
            for topic in (
                self.config.zmq.zone_enter_topic,
                self.config.zmq.zone_exit_topic,
            ):
                self.trigger_socket.setsockopt_string(zmq.SUBSCRIBE, topic)

            # Small delay to allow ZMQ to establish connection
            time.sleep(0.1)

            self.logger.info(
                f"ZMQ publisher bound to port {self.config.zmq.braid_port} with topic '{self.config.zmq.braid_topic}'; "
                f"active lens updates on port {self.config.zmq.active_braid_port} "
                f"with topic '{self.config.zmq.active_braid_topic}'"
            )
        except zmq.ZMQError as e:
            raise Exception(f"Failed to set up ZMQ publisher: {e}")

    def _handle_trigger_message(self, topic: str, payload: Dict[str, Any]) -> None:
        if topic == self.config.zmq.zone_enter_topic:
            obj_id = payload.get("obj_id")
            if obj_id is not None:
                self._active_obj_id = obj_id
        elif (
            topic == self.config.zmq.zone_exit_topic
            and payload.get("obj_id") == self._active_obj_id
        ):
            self._active_obj_id = None

    def _drain_trigger_events(self) -> None:
        if self.trigger_socket is None:
            return

        # Expire a stuck active object if Braid has stopped sending updates for it.
        if self._active_obj_id is not None and self._active_last_seen > 0:
            age = time.monotonic() - self._active_last_seen
            if age > self.config.zone_timeout:
                self.logger.warning(
                    f"Active object {self._active_obj_id} timed out "
                    f"({age:.2f}s > zone_timeout={self.config.zone_timeout}s) — clearing"
                )
                self._active_obj_id = None
                self._active_last_seen = 0.0

        while True:
            try:
                topic_b, raw = self.trigger_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                return

            try:
                self._handle_trigger_message(
                    topic_b.decode("utf-8"), json.loads(raw.decode("utf-8"))
                )
            except Exception as e:
                self.logger.error(f"Failed to process trigger event in BraidPublisher: {e}")

    def _dispatch_event(self, data_str: str) -> None:
        """Parse one SSE `data:` payload and forward it to ZMQ.

        Errors in a single event log and skip — they never abort the stream
        or drop sibling events.
        """
        if self.zmq_socket is None:
            return

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse SSE data JSON: {e}")
            return

        if "msg" not in data:
            return

        # Inject relay wall-clock into the inner payload (Update/Birth) so
        # consumers can read t_relay alongside the other fields. Death is a
        # scalar obj_id, skip it.
        msg = data["msg"]
        t_relay = time.time()
        for key in ("Update", "Birth"):
            inner = msg.get(key)
            if isinstance(inner, dict):
                inner["t_relay"] = t_relay
                break

        message = json.dumps(msg)
        self.zmq_socket.send_multipart([self._topic_bytes, message.encode("utf-8")])

        death_obj_id = msg.get("Death")
        if death_obj_id == self._active_obj_id:
            self._active_obj_id = None

        update = msg.get("Update")
        if (
            self.active_braid_socket is not None
            and isinstance(update, dict)
            and update.get("obj_id") == self._active_obj_id
        ):
            self._active_last_seen = time.monotonic()
            active_message = json.dumps(update)
            self.active_braid_socket.send_multipart(
                [self._active_topic_bytes, active_message.encode("utf-8")]
            )
        self.logger.debug("Published message: %.50s...", message)

    def _process_stream(self) -> None:
        """Process the event stream in a separate thread.

        Uses a line-driven SSE parser per the W3C spec: accumulates
        `event:` and `data:` fields across lines and dispatches on a blank
        line. This is robust to TCP coalescing multiple events into one read
        and to a single event split across reads — both of which silently
        dropped events under the previous chunk-based parser.
        """
        connection_attempts = 0

        while not self.stop_event.is_set():
            try:
                if self.session is None or self.events_url is None:
                    self.logger.error("Session or events URL not initialized")
                    time.sleep(1)
                    continue

                response = self.session.get(
                    self.events_url,
                    stream=True,
                    headers={"Accept": "text/event-stream"},
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                connection_attempts = 0

                self.logger.debug("Connected to event stream, processing events...")

                last_boundary = time.monotonic()

                for event_type, data_str in iter_sse_events(
                    response.iter_lines(decode_unicode=True)
                ):
                    if self.stop_event.is_set():
                        break

                    self._drain_trigger_events()
                    if event_type == "braid":
                        self._dispatch_event(data_str)

                        now = time.monotonic()
                        gap = now - last_boundary
                        if gap > BOUNDARY_GAP_WARN_S:
                            self.logger.warning(
                                f"SSE boundary gap {gap * 1000:.1f} ms "
                                f"(>{BOUNDARY_GAP_WARN_S * 1000:.0f} ms)"
                            )
                        last_boundary = now

            except (requests.RequestException, ConnectionError) as e:
                if self.stop_event.is_set():
                    break

                connection_attempts += 1
                retry_delay = min(self.config.reconnect_delay * connection_attempts, 30)

                self.logger.warning(
                    f"Connection error: {e}. Retrying in {retry_delay}s..."
                )
                time.sleep(retry_delay)

        self.logger.debug("Stream processing thread exited")

    def _run(self) -> None:
        """
        Main process function that runs the Braid subscriber.

        This starts the stream processing in a separate thread and
        monitors the stop event.
        """
        self.initialize()
        if not self.is_connected:
            self.logger.error("Failed to initialize, exiting process")
            return

        self.logger.debug("Starting BraidSubscriber process")

        # Start stream processing in a separate thread
        self.stream_thread = Thread(target=self._process_stream)
        self.stream_thread.daemon = True
        self.stream_thread.start()

        # Wait for stop event
        try:
            while not self.stop_event.is_set():
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass  # Graceful shutdown via stop_event

        self.logger.debug("Stop event received, cleaning up...")
        self.close()

    def close(self) -> None:
        """Clean up resources and connections."""
        self.logger.debug("Closing BraidSubscriber...")

        # Set stop event to signal threads to exit
        self.stop_event.set()

        # Wait for stream thread to exit before closing sockets it uses.
        if self.stream_thread and self.stream_thread.is_alive():
            self.logger.debug("Waiting for stream thread to exit")
            self.stream_thread.join(timeout=2)

        # Close ZMQ sockets and context
        for sock_attr in ("zmq_socket", "active_braid_socket", "trigger_socket"):
            sock = getattr(self, sock_attr, None)
            if sock:
                self.logger.debug(f"Closing {sock_attr}")
                sock.close()
                setattr(self, sock_attr, None)

        if self.zmq_context:
            self.logger.debug("Terminating ZMQ context")
            self.zmq_context.term()
            self.zmq_context = None

        # Close requests session
        if self.session:
            self.logger.debug("Closing requests session")
            self.session.close()
            self.session = None

        self.is_connected = False
        self.logger.info("BraidSubscriber closed successfully")


# Example usage when run directly
if __name__ == "__main__":
    import argparse

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Braid Subscriber Process")
    parser.add_argument(
        "--config", "-c", default="configs/config.toml", help="Path to config file"
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default="DEBUG",
        choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level",
    )
    args = parser.parse_args()

    configure_process_logging(
        None,
        "BraidPublisher",
        "BLUE",
        level=getattr(__import__("logging"), args.log_level.upper(), 20),
    )
    import logging

    logger = logging.getLogger(__name__)
    logger.info("Starting BraidSubscriber...")

    # Create and run subscriber
    stop_event = mp.Event()
    subscriber = BraidPublisher(config_path=args.config, event=stop_event)

    try:
        if subscriber.initialize():
            subscriber.start()
            logger.info("Press Ctrl+C to stop")

            # Wait for process to complete
            subscriber.join()
        else:
            logger.error("Failed to initialize subscriber")
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping subscriber...")
        stop_event.set()
        subscriber.join(timeout=3)
