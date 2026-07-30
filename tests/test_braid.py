import os

import pytest

from src.utils.braid import BraidFolderError, check_braid_folder_exists


def test_check_braid_folder_exists_raises_braid_folder_error_for_missing_root(tmp_path):
    missing_root = str(tmp_path / "does_not_exist")
    with pytest.raises(BraidFolderError, match="does not exist"):
        check_braid_folder_exists(
            missing_root, callback_url=None, auto_start_recording=False
        )


def test_check_braid_folder_exists_raises_braid_folder_error_when_no_folder_and_no_callback(
    tmp_path,
):
    root = str(tmp_path)
    os.makedirs(root, exist_ok=True)
    with pytest.raises(BraidFolderError, match="callback URL"):
        check_braid_folder_exists(root, callback_url=None, auto_start_recording=True)
