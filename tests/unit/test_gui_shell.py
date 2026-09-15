from fastapi.testclient import TestClient

from apps.api.main import Settings, create_app


def test_control_plane_serves_the_static_dashboard_shell() -> None:
    client = TestClient(create_app(Settings(database_url="postgresql://invalid")))

    response = client.get("/")

    assert response.status_code == 200
    assert "审计智能中枢" in response.text
    assert 'src="/web/app.js"' in response.text


def test_control_plane_serves_dashboard_assets() -> None:
    client = TestClient(create_app(Settings(database_url="postgresql://invalid")))

    response = client.get("/web/app.js")

    assert response.status_code == 200
    assert "policy/simulate" in response.text
