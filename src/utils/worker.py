import logging
import signal
from multiprocessing import Process, Event
from src.utils.logger import init_class_logger, setup_file_logging


class WorkerProcess(Process):
    # Class-level log path set by main before any process starts
    _log_path: str | None = None

    def __init__(
        self,
        event: Event,
        log_level: str = "INFO",
        log_color: str | None = None,
        process_name: str = "WorkerProcess",
    ):
        super().__init__()
        self.logger = None
        self.event = event
        self.log_level = log_level
        self.log_color = log_color
        self.process_name = process_name

    def start(self):
        """Start the process, ignoring SIGINT in children.

        Child processes should shut down via the shared stop event,
        not by receiving KeyboardInterrupt directly.
        """
        # Save original handler, ignore SIGINT before fork/spawn so
        # the child inherits the ignore. Restore in parent immediately.
        original = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        super().start()
        signal.signal(signal.SIGINT, original)

    def _initialize_logger(self):
        # Reset global logging.disable inherited from parent fork
        logging.disable(logging.NOTSET)

        # After fork the module-level _file_handler guard is stale
        # (copied from parent); reset it so the child opens its own handle.
        import src.utils.logger as _logger_mod
        _logger_mod._file_handler = None

        # Set up file logging if a path was configured
        if WorkerProcess._log_path:
            setup_file_logging(WorkerProcess._log_path)

        self.logger = init_class_logger(
            instance=self,
            log_level=self.log_level,
            log_color=self.log_color,
            process_name=self.process_name,
        )
        return self.logger
