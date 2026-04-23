import logging
import signal
from multiprocessing import Process, Event
from src.utils.logger import configure_process_logging


class WorkerProcess(Process):
    def __init__(
        self,
        event: Event,
        log_path: str | None = None,
        log_level: str = "INFO",
        log_color: str | None = None,
        process_name: str = "WorkerProcess",
    ):
        super().__init__()
        self.event = event
        self.log_path = log_path
        self.log_level = log_level
        self.log_color = log_color
        self.process_name = process_name
        self.logger = logging.getLogger(self.__class__.__module__)

    def start(self):
        original = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        super().start()
        signal.signal(signal.SIGINT, original)

    def run(self):
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        configure_process_logging(
            self.log_path,
            self.process_name,
            self.log_color,
            level=getattr(logging, self.log_level.upper(), logging.INFO),
        )
        self.logger = logging.getLogger(self.__class__.__module__)
        try:
            self._run()
        except Exception:
            if self.logger:
                self.logger.exception(f"{self.process_name} crashed in _run()")
            raise

    def _run(self):
        raise NotImplementedError
