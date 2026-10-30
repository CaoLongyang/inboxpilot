"""Contract and health tests.

These are the tests that must never break: if the API envelope or the
approval gate changes shape, B's frontend breaks silently.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import app

client = TestClient(app)


def test_health_returns_envelope() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "integrations" in body
    # Secrets must never be echoed back.
    assert "nebius_api_key" not in response.text.lower()


def test_request_id_header_is_present() -> None:
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_unknown_route_uses_the_same_envelope() -> None:
    # Every endpoint is implemented as of Day 4, so the envelope is now proven
    # on a path that genuinely does not exist rather than on a 501 stub.
    response = client.get("/no/such/route")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]
    assert body["error"]["request_id"]


def test_validation_error_uses_same_envelope() -> None:
    response = client.post("/analyze", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_email_send_is_disabled_by_default() -> None:
    assert get_settings().allow_email_send is False
