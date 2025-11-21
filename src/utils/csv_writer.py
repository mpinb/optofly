import csv
import os

from src.utils.logger import init_class_logger


class CSVWriter:
    def __init__(
        self,
        filepath: str,
        strict: bool = True,
        process_name: str = "CSVWriter",
        log_level: str = "INFO",
        log_color: str = "RED",
    ):
        """
        Initialize a CSVWriter for appending rows as dictionaries to a CSV file.

        Args:
            filepath (str): Path to the CSV file.
            strict (bool, optional): If True, raise error on missing headers. If False, use None for missing values. Defaults to True.
        """
        self.filepath = filepath
        self.headers = None
        self.has_header = False
        self.strict = strict
        self.file = None
        self.writer = None

        # Initialize logger using the utility function
        self.logger = init_class_logger(
            instance=self,
            log_level=log_level,
            process_name=process_name,
            log_color=log_color,
        )

        # Check if file exists and has headers
        self._check_file()

    def _check_file(self):
        """Check if the file exists and read its headers if it does."""
        if os.path.exists(self.filepath) and os.path.getsize(self.filepath) > 0:
            with open(self.filepath, "r", newline="") as f:
                reader = csv.reader(f)
                try:
                    self.headers = next(reader)
                    self.has_header = True
                except StopIteration:
                    # File exists but is empty
                    pass

    def _ensure_writer(self):
        """Ensure the file is open and the writer is ready."""
        if not self.file:
            self.file = open(self.filepath, "a", newline="")
            self.writer = csv.DictWriter(
                self.file, fieldnames=self.headers, restval=None, extrasaction="ignore"
            )

            if not self.has_header:
                self.writer.writeheader()
                self.has_header = True

    def append(self, row_dict: dict):
        """Append a single row to the CSV file.

        Args:
            row_dict (dict): A dictionary where keys are columns and values are row entries.

        Raises:
            ValueError: If strict is True and row_dict is missing required headers.
        """
        # If this is the first row, set headers
        if not self.headers:
            self.headers = list(row_dict.keys())

        # Check if the row dict has all the required headers
        if self.strict:
            missing_headers = set(self.headers) - set(row_dict.keys())
            if missing_headers:
                raise ValueError(f"Missing headers in row dict: {missing_headers}")

        # Ensure we have a writer
        self._ensure_writer()

        # Write the row
        self.writer.writerow(row_dict)
        self.file.flush()  # Ensure data is written to disk

    def append_many(self, row_dicts):
        """Append multiple rows to the CSV file.

        Args:
            row_dicts (list): A list of dictionaries, each representing a row.

        Raises:
            ValueError: If strict is True and any row_dict is missing required headers.
        """
        if not row_dicts:
            return

        # If this is the first row, set headers
        if not self.headers:
            self.headers = list(row_dicts[0].keys())

        # Check if all row dicts have all the required headers
        if self.strict:
            for i, row_dict in enumerate(row_dicts):
                missing_headers = set(self.headers) - set(row_dict.keys())
                if missing_headers:
                    raise ValueError(
                        f"Missing headers in row dict at index {i}: {missing_headers}"
                    )

        # Ensure we have a writer
        self._ensure_writer()

        # Write the rows
        self.writer.writerows(row_dicts)
        self.file.flush()  # Ensure data is written to disk

    def close(self):
        """Close the file if it's open."""
        if self.file:
            self.file.close()
            self.file = None
            self.writer = None

    def __del__(self):
        """Destructor to ensure the file is closed."""
        self.close()

    def __enter__(self):
        """Context manager entry point."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit point."""
        self.close()
