from multiprocessing import Process, Event
from src.utils.logger import init_class_logger


class WorkerProcess(Process):
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

    def _initialize_logger(self):
        self.logger = init_class_logger(
            instance=self,
            log_level=self.log_level,
            log_color=self.log_color,
            process_name=self.process_name,
            # init_message suppressed to reduce startup noise
        )
        return self.logger
