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


def test_unimplemented_endpoint_uses_error_envelope() -> None:
    # /analyze is implemented as of Day 3; /actions/plan lands on Day 4.
    response = client.post("/actions/plan", json={"message_id": "abc"})
    assert response.status_code == 501
    body = response.json()
    assert body["error"]["code"] == "not_implemented"
    assert body["error"]["message"]
    assert body["error"]["request_id"]


def test_validation_error_uses_same_envelope() -> None:
    response = client.post("/analyze", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_email_send_is_disabled_by_default() -> None:
    assert get_settings().allow_email_send is False
