from src.gui.app import create_app
from src.orchestration import Experiment


def test_api_status_reports_not_running():
    app = create_app(Experiment(), config_path=None)
    client = app.test_client()

    response = client.get("/api/status")

    assert response.status_code == 200
    data = response.get_json()
    assert data["running"] is False
    assert data["processes"] == {}


def test_root_redirects_to_run():
    app = create_app(Experiment(), config_path=None)
    client = app.test_client()

    response = client.get("/", follow_redirects=False)

    assert response.status_code in (301, 302)
    assert response.headers["Location"].endswith("/run")
