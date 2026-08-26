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


def colorize(text: str, color: str) -> str:
    """Wrap `text` in the ANSI code for `color` (see COLORS), reset after.

    For the plain print() banners (trial start/end, opto/visual summaries)
    that sit outside the logging system but should still carry their
    process's color so they read as part of that process's output.
    """
    return f"{COLORS.get(color.upper(), COLORS['WHITE'])}{text}{COLORS['RESET']}"


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
    console_level: int | None = None,
    file_level: int | None = None,
) -> None:
    """Configure the root logger for a process. Call once at process entry.

    console_level / file_level override `level` for their respective handlers.
    When both are None (the default), both handlers share `level` — backward
    compatible with every existing caller.
    """
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    logging.disable(logging.NOTSET)

    color_code = COLORS.get((color or "WHITE").upper(), COLORS["WHITE"])
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(ColoredFormatter(process_name, color_code))
    stream.setLevel(console_level if console_level is not None else level)
    root.addHandler(stream)

    effective_file_level: int | None = None
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file = logging.FileHandler(log_path, mode="a")
        file.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        effective_file_level = file_level if file_level is not None else level
        file.setLevel(effective_file_level)
        root.addHandler(file)

    effective_console = console_level if console_level is not None else level
    effective_file = (
        effective_file_level if effective_file_level is not None else effective_console
    )
    root.setLevel(min(effective_console, effective_file))
