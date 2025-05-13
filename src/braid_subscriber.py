"""
Braid Server subscriber that connects to a Braid server and publishes events to ZMQ.
"""

import json
import logging
import multiprocessing as mp
import signal
import sys
import time
from threading import Thread
from typing import Any, Dict, Optional

import requests
import zmq

from config import BraidSubscriberConfig

# Constants
DATA_PREFIX = "data: "
MAX_RETRIES = 5
RETRY_DELAY = 2

# Configure logging
logger = logging.getLogger(__name__)
# Set up logging format
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# Add console handler
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logger.setLevel(logging.DEBUG)  # Set to DEBUG for detailed output


def parse_chunk(chunk: str) -> Dict[str, Any]:
    """Parse a Server-Sent Events (SSE) chunk from Braid server.

    Args:
        chunk: Raw chunk data from the event stream

    Returns:
        Parsed JSON data from the chunk

    Raises:
        ValueError: If the chunk format is invalid
    """
    lines = chunk.strip().split("\n")

    if len(lines) != 2:
        raise ValueError(f"Expected 2 lines in chunk, got {len(lines)}")

    if lines[0] != "event: braid":
        raise ValueError(f"Expected 'event: braid', got '{lines[0]}'")

    if not lines[1].startswith(DATA_PREFIX):
        raise ValueError(
            f"Expected line starting with '{DATA_PREFIX}', got '{lines[1]}'"
        )

    data_str = lines[1][len(DATA_PREFIX) :]

    try:
        data = json.loads(data_str)
        return data
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}")


class BraidSubscriber(mp.Process):
    """
    Process that subscribes to a Braid server and publishes events to ZMQ.

    This class establishes a connection to a Braid server's event stream and
    forwards messages to a ZMQ PUB socket for other processes to consume.
    """

    def __init__(
        self, config_path: str = "config.toml", event: Optional[mp.Event] = None # type: ignore
    ):  # type: ignore
        """
        Initialize the BraidSubscriber.

        Args:
            config_path: Path to the configuration file
            event: Event to signal process termination (created if None)
        """
        super().__init__()
        self.config = BraidSubscriberConfig(config_path)
        self.stop_event = event if event is not None else mp.Event()

        # Connection objects (initialized later)
        self.session = None
        self.events_url = None
        self.zmq_context = None
        self.zmq_socket = None
        self.stream_thread = None
        self.is_connected = False

        # For handling signals in the process
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """Handle termination signals by setting the stop event."""
        logger.debug(f"Received signal {signum}, shutting down...")
        self.stop_event.set()

    def initialize(self) -> bool:
        """
        Initialize connections to Braid server and ZMQ.

        Returns:
            True if both connections were established successfully, False otherwise
        """
        try:
            self._connect_to_braid()
            self._connect_to_zmq()
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"Failed to initialize BraidSubscriber: {e}")
            self.close()
            return False

    def _connect_to_braid(self) -> None:
        """
        Connect to the Braid server's event stream.

        Raises:
            Exception: If connection fails
        """
        logger.debug(f"Connecting to Braid server at {self.config.url}")

        self.session = requests.Session()

        # Test the connection
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(self.config.url, timeout=self.config.timeout)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    logger.warning(
                        f"Connection attempt {attempt + 1}/{MAX_RETRIES} failed: {e}"
                    )
                    time.sleep(RETRY_DELAY)
                else:
                    raise Exception(
                        f"Failed to connect to {self.config.url} after {MAX_RETRIES} attempts: {e}"
                    )

        # Prepare events URL
        self.events_url = f"{self.config.url.rstrip('/')}/events"
        logger.info(
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
            bind_address = self.config.zmq.get_publisher_address(self.config.zmq.braid_port)

            logger.debug(f"Binding ZMQ publisher to {bind_address}")
            self.zmq_socket.bind(bind_address)

            # Small delay to allow ZMQ to establish connection
            time.sleep(0.1)

            logger.info(
                f"ZMQ publisher bound to port {self.config.zmq.braid_port} with topic '{self.config.zmq.braid_topic}'"
            )
        except zmq.ZMQError as e:
            raise Exception(f"Failed to set up ZMQ publisher: {e}")

    def _process_stream(self) -> None:
        """Process the event stream in a separate thread."""
        connection_attempts = 0

        while not self.stop_event.is_set():
            try:
                # Get event stream with timeout
                response = self.session.get( # type: ignore
                    self.events_url, # type: ignore
                    stream=True,
                    headers={"Accept": "text/event-stream"},
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                connection_attempts = 0

                logger.debug("Connected to event stream, processing events...")

                # Process events
                for chunk in response.iter_content(
                    chunk_size=None, decode_unicode=True
                ):
                    if self.stop_event.is_set():
                        break

                    try:
                        data = parse_chunk(chunk)

                        if "msg" in data:
                            # Publish to ZMQ
                            message = json.dumps(data["msg"])
                            self.zmq_socket.send_string( # type: ignore
                                f"{self.config.zmq.braid_topic} {message}"
                            )
                            logger.debug(f"Published message: {message[:50]}...")
                    except Exception as e:
                        logger.error(f"Error processing chunk: {e}")

            except (requests.RequestException, ConnectionError) as e:
                if self.stop_event.is_set():
                    break

                connection_attempts += 1
                retry_delay = min(self.config.reconnect_delay * connection_attempts, 30)

                logger.error(f"Connection error: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)

        logger.debug("Stream processing thread exited")

    def run(self) -> None:
        """
        Main process function that runs the Braid subscriber.

        This starts the stream processing in a separate thread and
        monitors the stop event.
        """
        if not self.is_connected and not self.initialize():
            logger.error("Failed to initialize, exiting process")
            return

        logger.debug("Starting BraidSubscriber process")

        # Start stream processing in a separate thread
        self.stream_thread = Thread(target=self._process_stream)
        self.stream_thread.daemon = True
        self.stream_thread.start()

        # Wait for stop event
        while not self.stop_event.is_set():
            time.sleep(0.1)

        logger.debug("Stop event received, cleaning up...")
        self.close()

    def close(self) -> None:
        """Clean up resources and connections."""
        logger.debug("Closing BraidSubscriber...")

        # Set stop event to signal threads to exit
        self.stop_event.set()

        # Close ZMQ socket and context
        if self.zmq_socket:
            logger.debug("Closing ZMQ socket")
            self.zmq_socket.close()
            self.zmq_socket = None

        if self.zmq_context:
            logger.debug("Terminating ZMQ context")
            self.zmq_context.term()
            self.zmq_context = None

        # Close requests session
        if self.session:
            logger.debug("Closing requests session")
            self.session.close()
            self.session = None

        # Wait for stream thread to exit
        if self.stream_thread and self.stream_thread.is_alive():
            logger.debug("Waiting for stream thread to exit")
            self.stream_thread.join(timeout=2)

        self.is_connected = False
        logger.info("BraidSubscriber closed successfully")


# Example usage when run directly
if __name__ == "__main__":
    import sys

    # Create and run subscriber
    stop_event = mp.Event()
    subscriber = BraidSubscriber(event=stop_event)

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