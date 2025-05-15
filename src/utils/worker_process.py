from multiprocessing import Process, Event
from src.utils.custom_logger import init_class_logger


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
            instance=self,  # Changed from self.__class__.__name__ to instance=self
            log_level=self.log_level,
            log_color=self.log_color,  # Changed from log_color to color to match parameter name
            process_name=self.process_name,
            init_message=f"Logger initialized for {self.process_name} with level {self.log_level} and color {self.log_color}",
        )
        return self.logger
