from unittest.mock import MagicMock

import pytest

from src.gui.app import create_app
from src.orchestration import ExperimentAlreadyRunningError, ExperimentStartError


@pytest.fixture
def experiment():
    return MagicMock()


@pytest.fixture
def client(experiment):
    app = create_app(experiment)
    return app.test_client()


def test_get_run_page_renders(client):
    response = client.get("/run")
    assert response.status_code == 200
    assert b"Start" in response.data


def test_prepare_returns_braid_folder(client, experiment):
    experiment.prepare_braid_folder.return_value = "/mnt/data/experiments/20260101_000000.braid"

    response = client.post("/run/prepare", data={"config_path": "configs/config.toml"})

    assert response.status_code == 200
    assert response.get_json()["braid_folder"] == "/mnt/data/experiments/20260101_000000.braid"


def test_start_calls_experiment_with_form_metadata(client, experiment):
    response = client.post(
        "/run/start",
        data={
            "config_path": "configs/config.toml",
            "experimenter": "Jane",
            "n_flies": "10",
            "experiment_duration": "4",
        },
    )

    assert response.status_code == 200
    experiment.start.assert_called_once()
    call_args = experiment.start.call_args
    assert call_args.args[0] == "configs/config.toml"
    assert call_args.args[1]["experimenter"] == "Jane"
    assert call_args.args[1]["n_flies"] == 10


def test_start_when_already_running_returns_error_not_500(client, experiment):
    experiment.start.side_effect = ExperimentAlreadyRunningError("already running")

    response = client.post("/run/start", data={"config_path": "configs/config.toml"})

    assert response.status_code == 409
    assert "already running" in response.get_json()["error"]


def test_start_failure_returns_error_not_500(client, experiment):
    experiment.start.side_effect = ExperimentStartError("OptoTriggerWorker exited during init")

    response = client.post("/run/start", data={"config_path": "configs/config.toml"})

    assert response.status_code == 422
    assert "OptoTriggerWorker" in response.get_json()["error"]


def test_stop_calls_experiment_stop(client, experiment):
    response = client.post("/run/stop")

    assert response.status_code == 200
    experiment.stop.assert_called_once()
