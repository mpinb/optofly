import logging
import sys

# ANSI color codes for terminal output
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


class ColoredFormatter(logging.Formatter):
    """Custom formatter that includes color."""

    def __init__(self, process_name, color_code, fmt=None, datefmt=None):
        self.process_name = process_name
        self.color_code = color_code
        super().__init__(fmt, datefmt)

    def format(self, record):
        # Add the process name to the record
        record.processName = self.process_name
        # Format the record
        formatted_message = super().format(record)
        # Add color
        return f"{self.color_code}{formatted_message}{COLORS['RESET']}"


def get_logger(module_name, process_name=None, color=None, log_level=None):
    """
    Get a logger configured with the given settings.

    Args:
        module_name (str): The name of the module (typically __name__).
        process_name (str, optional): Process name to display in logs. Defaults to current process name.
        color (str, optional): Color name from COLORS dict. Defaults to WHITE.
        log_level (int, optional): Logging level. If None, doesn't change the level.

    Returns:
        logging.Logger: Configured logger instance.
    """
    # Get or create a logger for the module
    logger = logging.getLogger(module_name)

    # Only configure the logger if it hasn't been configured yet
    if not logger.handlers:
        # Set process name (default to current process name if not provided)
        if process_name is None:
            import multiprocessing as mp

            process_name = mp.current_process().name

        # Set color (default to WHITE if not provided or invalid)
        color_code = COLORS.get(color.upper() if color else None, COLORS["WHITE"])

        # Create a handler
        handler = logging.StreamHandler(sys.stdout)

        # Create a formatter
        formatter = ColoredFormatter(
            process_name=process_name,
            color_code=color_code,
            fmt="[%(asctime)s - %(processName)s - %(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Set the log level if provided
    if log_level is not None:
        logger.setLevel(log_level)

    return logger


def init_class_logger(
    instance, log_level=None, process_name=None, log_color=None, init_message=None
) -> logging.Logger:
    """
    Initialize a logger for a class instance.

    Args:
        instance: The class instance to initialize the logger for
        log_level: The logging level (if None, logger won't be initialized)
        process_name: Process name to display in logs (defaults to class name)
        log_color: Color name from COLORS dict
        init_message: Optional message to log at INFO level upon initialization

    Returns:
        logging.Logger or None: The configured logger or None if log_level is None
    """

    # If process_name is not provided, use the class name
    if process_name is None:
        process_name = instance.__class__.__name__

    # Get the module name from the class
    module_name = instance.__class__.__module__

    # Initialize the logger
    logger = get_logger(
        module_name=module_name,
        process_name=process_name,
        color=log_color,
        log_level=log_level,
    )

    # Log the initialization message if provided
    if init_message:
        logger.info(init_message)

    return logger
