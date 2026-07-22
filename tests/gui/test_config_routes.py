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


def test_get_config_page_renders(client):
    response = client.get("/config?path=configs/config.example.toml&kind=config")
    assert response.status_code == 200
    assert b"opto_trigger" in response.data or b"color" in response.data


def test_save_updates_file(client, config_copy):
    response = client.post(
        "/config/save",
        data={
            "path": str(config_copy),
            "kind": "config",
            "opto_trigger.active": "on",
            "opto_trigger.color": "blue",
            "opto_trigger.duration": "100,200",
            "opto_trigger.intensity": "50",
            "opto_trigger.frequency": "0",
            "opto_trigger.sham_probability": "0.1",
            "camera.active": "on",
            "visual_stimuli.active": "on",
            "monitoring.active": "on",
        },
    )
    assert response.status_code == 200

    text = config_copy.read_text()
    assert 'color = "blue"' in text
    assert "# LED optogenetic stimulation" in text  # comment preserved


def test_save_invalid_path_returns_error_not_500(client):
    response = client.post(
        "/config/save",
        data={"path": "/nonexistent/config.toml", "kind": "config"},
    )
    assert response.status_code == 422
