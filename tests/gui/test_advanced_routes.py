import shutil

import pytest

from src.gui.app import create_app
from src.orchestration import Experiment


@pytest.fixture
def config_copy(tmp_path):
    dest = tmp_path / "config.toml"
    shutil.copy("configs/config.example.toml", dest)
    return dest


@pytest.fixture
def client():
    app = create_app(Experiment())
    return app.test_client()


def test_get_advanced_page_shows_file_contents(client, config_copy):
    response = client.get(f"/advanced?path={config_copy}")
    assert response.status_code == 200
    assert b"opto_trigger" in response.data


def test_save_valid_toml_writes_file(client, config_copy):
    new_text = config_copy.read_text() + '\n[extra]\nkey = "value"\n'

    response = client.post("/advanced/save", data={"path": str(config_copy), "content": new_text})

    assert response.status_code == 200
    assert 'key = "value"' in config_copy.read_text()


def test_save_invalid_toml_rejected_without_writing(client, config_copy):
    original_text = config_copy.read_text()

    response = client.post(
        "/advanced/save", data={"path": str(config_copy), "content": "this is not [valid toml"}
    )

    assert response.status_code == 422
    assert "error" in response.get_json()
    assert config_copy.read_text() == original_text  # untouched
