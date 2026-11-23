from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_page_is_the_source_health_screen() -> None:
    # /health is now the "Source health" page shell; the JSON check moved under /api/v1.
    response = client.get("/health")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Source health" in response.text
