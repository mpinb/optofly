import logging
import os
import sys

COLORS = {
    "RED": "\033[91m",
    "GREEN": "\033[92m",
    "YELLOW": "\033[93m",
    "BLUE": "\033[94m",
    "MAGENTA": "\033[95m",
    "CYAN": "\033[96m",
    "WHITE": "\033[97m",
    "RESET": "\033[0m",
}

_FMT = "[%(asctime)s - %(processName)s - %(name)s] %(levelname)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class ColoredFormatter(logging.Formatter):
    def __init__(self, process_name, color_code):
        super().__init__(_FMT, _DATEFMT)
        self.process_name = process_name
        self.color_code = color_code

    def format(self, record):
        record.processName = self.process_name
        return f"{self.color_code}{super().format(record)}{COLORS['RESET']}"


def configure_process_logging(
    log_path: str | None,
    process_name: str,
    color: str | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure the root logger for a process. Call once at process entry."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    logging.disable(logging.NOTSET)

    color_code = COLORS.get((color or "WHITE").upper(), COLORS["WHITE"])
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(ColoredFormatter(process_name, color_code))
    root.addHandler(stream)

    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file = logging.FileHandler(log_path, mode="a")
        file.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        root.addHandler(file)

    root.setLevel(level)
