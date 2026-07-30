"""Background thread: subscribes to ZONE_ENTER over ZMQ, tails opto.csv and
stim.csv for newly-written rows, and feeds MonitorState (which fans both
out to any subscribed SSE clients).

Runs for the lifetime of the GUI process (started once by the app
factory), independent of whether an experiment is currently running.
"""

import json
import os
import threading
from typing import Optional

import zmq

from src.gui.csv_tail import CSVTailer
from src.gui.monitor_state import MonitorState
from src.orchestration import Experiment
from src.utils.config import ZMQConfig


def monitor_loop(
    zmq_config: ZMQConfig,
    experiment: Experiment,
    state: MonitorState,
    stop_event: threading.Event,
    poll_interval_ms: int = 200,
) -> None:
    context = zmq.Context()
    socket = context.socket(zmq.SUB)
    socket.connect(zmq_config.get_subscriber_address(zmq_config.trigger_port))
    socket.setsockopt_string(zmq.SUBSCRIBE, zmq_config.zone_enter_topic)
    socket.setsockopt(zmq.RCVTIMEO, poll_interval_ms)

    current_braid_folder: Optional[str] = None
    opto_tailer: Optional[CSVTailer] = None
    stim_tailer: Optional[CSVTailer] = None

    try:
        while not stop_event.is_set():
            try:
                _topic, message = socket.recv_multipart()
                data = json.loads(message.decode("utf-8"))
                state.add_trigger(data)
            except zmq.Again:
                pass

            if experiment.needs_cleanup():
                # The shared stop event was set by something other than a
                # normal stop() call (e.g. a worker process crashed mid-run).
                # Nothing else joins/terminates processes for the GUI the way
                # main.py's finally block does for the CLI, so do it here.
                experiment.stop()

            braid_folder = experiment.status().get("braid_folder")
            if braid_folder != current_braid_folder:
                current_braid_folder = braid_folder
                opto_tailer = CSVTailer(os.path.join(braid_folder, "opto.csv")) if braid_folder else None
                stim_tailer = CSVTailer(os.path.join(braid_folder, "stim.csv")) if braid_folder else None

            if opto_tailer is not None:
                for row in opto_tailer.poll():
                    state.enrich("opto", row)
            if stim_tailer is not None:
                for row in stim_tailer.poll():
                    state.enrich("stim", row)
    finally:
        socket.close()
        context.term()


def start_monitor_thread(
    config_path: str, experiment: Experiment, state: MonitorState, stop_event: threading.Event
) -> threading.Thread:
    zmq_config = ZMQConfig.from_path(config_path)
    thread = threading.Thread(
        target=monitor_loop, args=(zmq_config, experiment, state, stop_event), daemon=True
    )
    thread.start()
    return thread
