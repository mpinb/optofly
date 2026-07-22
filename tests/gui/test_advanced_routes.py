import shutil
from pathlib import Path

import pytest

from src.gui.app import create_app
from src.orchestration import Experiment


@pytest.fixture
def client():
    app = create_app(Experiment())
    return app.test_client()


@pytest.fixture
def backup_config():
    """Backup and restore configs/config.toml before/after test."""
    config_path = Path("configs/config.toml")
    backup_path = Path("configs/config.toml.backup")

    # Create from example if it doesn't exist
    if not config_path.exists():
        shutil.copy("configs/config.example.toml", config_path)

    # Backup before test
    if config_path.exists():
        shutil.copy(config_path, backup_path)

    yield config_path

    # Restore after test
    if backup_path.exists():
        shutil.copy(backup_path, config_path)
        backup_path.unlink()
    elif config_path.exists():
        config_path.unlink()


@pytest.fixture
def backup_visual_stimuli():
    """Backup and restore configs/visual_stimuli.toml before/after test."""
    config_path = Path("configs/visual_stimuli.toml")
    backup_path = Path("configs/visual_stimuli.toml.backup")

    # Create from example if it doesn't exist
    if not config_path.exists():
        shutil.copy("configs/visual_stimuli.example.toml", config_path)

    # Backup before test
    if config_path.exists():
        shutil.copy(config_path, backup_path)

    yield config_path

    # Restore after test
    if backup_path.exists():
        shutil.copy(backup_path, config_path)
        backup_path.unlink()
    elif config_path.exists():
        config_path.unlink()


def test_get_advanced_page_shows_file_contents(client, backup_config):
    response = client.get("/advanced?path=configs/config.toml")
    assert response.status_code == 200
    assert b"opto_trigger" in response.data


def test_save_valid_toml_writes_file(client, backup_config):
    original_text = backup_config.read_text()
    new_text = original_text + '\n[extra]\nkey = "value"\n'

    response = client.post("/advanced/save", data={"path": "configs/config.toml", "content": new_text})

    assert response.status_code == 200
    assert 'key = "value"' in backup_config.read_text()


def test_save_invalid_toml_rejected_without_writing(client, backup_config):
    original_text = backup_config.read_text()

    response = client.post(
        "/advanced/save", data={"path": "configs/config.toml", "content": "this is not [valid toml"}
    )

    assert response.status_code == 422
    assert "error" in response.get_json()
    assert backup_config.read_text() == original_text  # untouched


def test_get_advanced_disallows_arbitrary_paths(client):
    """GET /advanced?path=/etc/passwd returns 404, not file contents."""
    response = client.get("/advanced?path=/etc/passwd")
    assert response.status_code == 404
    assert b"root:" not in response.data  # Should not contain /etc/passwd contents


def test_post_advanced_save_disallows_arbitrary_paths(client, tmp_path):
    """POST /advanced/save with disallowed path returns 422 and doesn't write."""
    disallowed_path = str(tmp_path / "should_not_be_written.toml")

    response = client.post(
        "/advanced/save",
        data={"path": disallowed_path, "content": '[valid]\ntoml = "content"\n'}
    )

    assert response.status_code == 422
    assert "error" in response.get_json()
    assert not Path(disallowed_path).exists()  # File should not be created
