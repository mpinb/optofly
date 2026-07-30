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
        file_log_level: str = "DEBUG",
        log_color: str | None = None,
        process_name: str = "WorkerProcess",
        failure_queue=None,
    ):
        """
        Args:
            failure_queue: Optional mp.Queue. When _run() raises, the worker
                puts (process_name, "ExcType: message") on it before dying, so
                the parent can report the actual cause instead of guessing from
                a static per-process hint. Optional because the standalone
                `python -m src.processes.<x>` entry points construct workers
                without a parent to report to.
        """
        super().__init__()
        self.event = event
        self.log_path = log_path
        self.log_level = log_level
        self.file_log_level = file_log_level
        self.log_color = log_color
        self.process_name = process_name
        self.failure_queue = failure_queue
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
            console_level=getattr(logging, self.log_level.upper(), logging.INFO),
            file_level=getattr(logging, self.file_log_level.upper(), logging.DEBUG),
        )
        self.logger = logging.getLogger(self.__class__.__module__)
        try:
            self._run()
        except Exception as e:
            self._report_failure(e)
            if self.logger:
                self.logger.exception(f"{self.process_name} crashed in _run()")
            raise

    def _report_failure(self, exc: Exception) -> None:
        """Hand the parent the real reason this process is about to die.

        Best-effort: a failure here must never mask the original exception,
        which is about to propagate and be logged either way.
        """
        if self.failure_queue is None:
            return
        try:
            self.failure_queue.put((self.process_name, f"{type(exc).__name__}: {exc}"))
        except Exception:
            pass

    def _run(self):
        raise NotImplementedError
