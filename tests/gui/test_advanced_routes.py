import shutil
from pathlib import Path

import pytest

from src.gui.app import create_app
from src.orchestration import Experiment


@pytest.fixture
def client():
    app = create_app(Experiment(), config_path=None)
    return app.test_client()


@pytest.fixture
def config_copy(tmp_path):
    """Copy config.example.toml to isolated tmp_path for testing."""
    dest = tmp_path / "config.toml"
    shutil.copy("configs/config.example.toml", dest)
    return dest


@pytest.fixture
def visual_stimuli_copy(tmp_path):
    """Copy visual_stimuli.example.toml to isolated tmp_path for testing."""
    dest = tmp_path / "visual_stimuli.toml"
    shutil.copy("configs/visual_stimuli.example.toml", dest)
    return dest


def test_get_advanced_page_shows_file_contents(client, config_copy, monkeypatch):
    """Test GET /advanced works with ALLOWED path (monkeypatched to tmp_path)."""
    path_str = str(config_copy)
    monkeypatch.setattr("src.gui.advanced_routes.ALLOWED_PATHS", {path_str})

    response = client.get(f"/advanced?path={path_str}")
    assert response.status_code == 200
    assert b"opto_trigger" in response.data


def test_save_valid_toml_writes_file(client, config_copy, monkeypatch):
    """Test POST /advanced/save with valid TOML writes to tmp_path file."""
    path_str = str(config_copy)
    monkeypatch.setattr("src.gui.advanced_routes.ALLOWED_PATHS", {path_str})

    original_text = config_copy.read_text()
    new_text = original_text + '\n[extra]\nkey = "value"\n'

    response = client.post("/advanced/save", data={"path": path_str, "content": new_text})

    assert response.status_code == 200
    assert 'key = "value"' in config_copy.read_text()


def test_save_invalid_toml_rejected_without_writing(client, config_copy, monkeypatch):
    """Test POST /advanced/save rejects invalid TOML and leaves file untouched."""
    path_str = str(config_copy)
    monkeypatch.setattr("src.gui.advanced_routes.ALLOWED_PATHS", {path_str})

    original_text = config_copy.read_text()

    response = client.post(
        "/advanced/save", data={"path": path_str, "content": "this is not [valid toml"}
    )

    assert response.status_code == 422
    assert "error" in response.get_json()
    assert config_copy.read_text() == original_text  # untouched


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
