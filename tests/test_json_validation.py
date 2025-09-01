"""Tests for JSON validation error responses."""

from fastapi.testclient import TestClient
import app.main as app_main


def test_characters_validation_error_is_problem_json():
    """Verify that validation errors on /characters return 422 problem+json."""
    client = TestClient(app_main.app)
    # page=0 violates ge=1, triggers 422
    r = client.get("/characters?page=0&page_size=10&sort=id&order=asc")
    assert r.status_code == 422
    assert r.headers.get("content-type", "").startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 422
    assert body["title"] == "Unprocessable Entity"
    assert isinstance(body["detail"], str) and body["detail"]
