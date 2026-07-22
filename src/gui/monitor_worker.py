"""Background thread: subscribes to ZONE_ENTER over ZMQ and feeds MonitorState.

Runs for the lifetime of the GUI process (started once by the app factory),
independent of whether an experiment is currently running — it simply
receives nothing while TriggerHandler isn't publishing.
"""

import json
import threading

import zmq

from src.gui.monitor_state import MonitorState
from src.utils.config import ZMQConfig


def monitor_loop(
    zmq_config: ZMQConfig,
    state: MonitorState,
    stop_event: threading.Event,
    poll_interval_ms: int = 200,
) -> None:
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(zmq_config.get_subscriber_address(zmq_config.trigger_port))
    socket.setsockopt_string(zmq.SUBSCRIBE, zmq_config.zone_enter_topic)
    socket.setsockopt(zmq.RCVTIMEO, poll_interval_ms)

    try:
        while not stop_event.is_set():
            try:
                _topic, message = socket.recv_multipart()
            except zmq.Again:
                continue
            data = json.loads(message.decode("utf-8"))
            state.add_trigger(data)
    finally:
        socket.close()
        context.term()


def start_monitor_thread(
    config_path: str, state: MonitorState, stop_event: threading.Event
) -> threading.Thread:
    zmq_config = ZMQConfig(config_path)
    thread = threading.Thread(
        target=monitor_loop, args=(zmq_config, state, stop_event), daemon=True
    )
    thread.start()
    return thread
