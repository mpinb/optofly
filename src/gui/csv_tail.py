"""Incrementally reads newly-appended rows from a CSV file that may not
exist yet (opto.csv / stim.csv are created lazily, only once the first
trigger fires). Byte-offset tracking assumes single-byte-per-character
content — true for these CSVs (numeric fields, color names, True/False),
never containing multi-byte unicode.
"""

import csv
import os


class CSVTailer:
    def __init__(self, path: str):
        self.path = path
        self._offset = 0
        self._fieldnames: list[str] | None = None

    def poll(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []

        with open(self.path, "r", newline="") as f:
            f.seek(self._offset)
            lines = f.readlines()
            end_pos = f.tell()

        if not lines:
            return []

        incomplete = None
        if not lines[-1].endswith("\n"):
            incomplete = lines.pop()

        if not lines:
            return []  # only a partial line available; wait for more data

        self._offset = end_pos - (len(incomplete) if incomplete else 0)

        if self._fieldnames is None:
            header_line = lines.pop(0)
            self._fieldnames = next(csv.reader([header_line]))
            if not lines:
                return []

        return [dict(zip(self._fieldnames, values)) for values in csv.reader(lines)]
