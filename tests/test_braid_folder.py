from datetime import datetime
from pathlib import Path

import src.utils.braid as braid_module
from src.utils.braid import check_braid_folder_exists


class _FakeBraidProxy:
    """Stands in for BraidProxy -- records that recording was started and
    creates a new dated folder when told to, mimicking real Braid."""

    def __init__(self, root_path: Path, folder_name: str):
        self._root_path = root_path
        self._folder_name = folder_name
        self.recording_started = False

    def start_csv_recording(self):
        self.recording_started = True
        (self._root_path / self._folder_name).mkdir()

    def stop_csv_recording(self):
        pass


def test_second_run_same_day_gets_a_fresh_folder_and_proxy(tmp_path, monkeypatch):
    """Regression: an existing same-day folder must not be silently reused
    with no proxy on a second run -- the first run already stopped
    recording into it on exit, so reusing it drops the second run's Braid
    tracking data entirely."""
    root = tmp_path
    today = datetime.now().strftime("%Y%m%d")
    first_folder = f"{today}_090000.braid"
    (root / first_folder).mkdir()

    second_folder = f"{today}_100000.braid"
    fake_proxy = _FakeBraidProxy(root, second_folder)
    monkeypatch.setattr(braid_module, "BraidProxy", lambda callback_url: fake_proxy)

    braid_folder, proxy = check_braid_folder_exists(
        str(root), callback_url="http://fake", auto_start_recording=True
    )

    assert Path(braid_folder).name == second_folder
    assert proxy is fake_proxy
    assert fake_proxy.recording_started is True


def test_first_run_of_the_day_also_starts_fresh(tmp_path, monkeypatch):
    root = tmp_path
    today = datetime.now().strftime("%Y%m%d")
    folder = f"{today}_090000.braid"
    fake_proxy = _FakeBraidProxy(root, folder)
    monkeypatch.setattr(braid_module, "BraidProxy", lambda callback_url: fake_proxy)

    braid_folder, proxy = check_braid_folder_exists(
        str(root), callback_url="http://fake", auto_start_recording=True
    )

    assert Path(braid_folder).name == folder
    assert proxy is fake_proxy


def test_explicit_check_only_mode_still_returns_existing_folder_with_no_proxy(tmp_path):
    """auto_start_recording=False keeps its original 'just check, don't
    start anything' behavior -- only the auto_start_recording=True path
    changes for this fix."""
    root = tmp_path
    today = datetime.now().strftime("%Y%m%d")
    folder = f"{today}_090000.braid"
    (root / folder).mkdir()

    braid_folder, proxy = check_braid_folder_exists(
        str(root), callback_url=None, auto_start_recording=False
    )

    assert Path(braid_folder).name == folder
    assert proxy is None
