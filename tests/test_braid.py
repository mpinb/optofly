import os

import pytest

from src.utils.braid import BraidFolderError, check_braid_folder_exists


def test_check_braid_folder_exists_raises_braid_folder_error_for_missing_root(tmp_path):
    missing_root = str(tmp_path / "does_not_exist")
    with pytest.raises(BraidFolderError, match="does not exist"):
        check_braid_folder_exists(missing_root, callback_url=None, auto_start_recording=False)


def test_check_braid_folder_exists_raises_braid_folder_error_when_no_folder_and_no_callback(tmp_path):
    root = str(tmp_path)
    os.makedirs(root, exist_ok=True)
    with pytest.raises(BraidFolderError, match="callback URL"):
        check_braid_folder_exists(root, callback_url=None, auto_start_recording=True)


def test_no_folder_no_auto_start_raises(tmp_path):
    with pytest.raises(BraidFolderError):
        check_braid_folder_exists(str(tmp_path), auto_start_recording=False)


def test_existing_folder_returns_path_no_exception(tmp_path):
    from datetime import datetime

    today = datetime.now().strftime("%Y%m%d")
    folder = tmp_path / f"{today}_120000.braid"
    folder.mkdir()

    found, proxy = check_braid_folder_exists(str(tmp_path), auto_start_recording=False)
    assert found == str(folder)
    assert proxy is None
