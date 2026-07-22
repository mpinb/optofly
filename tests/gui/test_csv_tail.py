import csv

from src.gui.csv_tail import CSVTailer


def _write_row(path, fieldnames, row, mode="a", header=False):
    with open(path, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if header:
            writer.writeheader()
        writer.writerow(row)


def test_poll_returns_nothing_for_missing_file(tmp_path):
    tailer = CSVTailer(str(tmp_path / "missing.csv"))
    assert tailer.poll() == []


def test_poll_returns_rows_appended_since_last_call(tmp_path):
    path = tmp_path / "opto.csv"
    fieldnames = ["obj_id", "frame", "color", "sham"]
    _write_row(path, fieldnames, {"obj_id": 1, "frame": 100, "color": "red", "sham": False}, header=True)

    tailer = CSVTailer(str(path))
    rows = tailer.poll()
    assert rows == [{"obj_id": "1", "frame": "100", "color": "red", "sham": "False"}]

    assert tailer.poll() == []  # nothing new

    _write_row(path, fieldnames, {"obj_id": 2, "frame": 200, "color": "blue", "sham": True})
    rows = tailer.poll()
    assert rows == [{"obj_id": "2", "frame": "200", "color": "blue", "sham": "True"}]


def test_poll_waits_for_a_complete_line(tmp_path):
    path = tmp_path / "stim.csv"
    with open(path, "w") as f:
        f.write("obj_id,frame\n1,100")  # no trailing newline yet — incomplete

    tailer = CSVTailer(str(path))
    assert tailer.poll() == []  # header alone with no complete data row

    with open(path, "a") as f:
        f.write("\n")  # completes the row

    assert tailer.poll() == [{"obj_id": "1", "frame": "100"}]
