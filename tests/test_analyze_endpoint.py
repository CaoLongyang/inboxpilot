"""`/analyze` + cache behaviour (tasks A8/A9).

These tests fake both external calls, so they run with no Google token and no
Nebius key. What they prove is the part that belongs to us: the second identical
request must not touch the network, and a failed analysis must not be cached.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.errors import NotFound, UpstreamError
from backend.main import app
from backend.routers import analyze as analyze_router
from backend.schemas import (
    ClaimKind,
    EmailAnalysis,
    EmailCategory,
    EmailDetail,
    ModelUsage,
)

client = TestClient(app)


def fake_email() -> EmailDetail:
    return EmailDetail(
        id="msg-cache",
        thread_id="thread-cache",
        subject="Assignment slides",
        sender="lecturer@example.com",
        date="2026-10-20T09:00:00Z",
        snippet="Please submit the slides by 24 October 2026.",
        body_text="Hi, please submit the slides by 24 October 2026. Thanks.",
    )


def fake_analysis() -> EmailAnalysis:
    return EmailAnalysis(
        message_id="msg-cache",
        thread_id="thread-cache",
        summary="A lecturer asks for the assignment slides.",
        category=EmailCategory.ACTION_REQUIRED,
        action_required=True,
        model="nvidia/llama-3.1-nemotron-70b-instruct",
    )


@pytest.fixture
def fake_pipeline(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Count how many times the expensive path actually runs."""
    calls = {"gmail": 0, "model": 0}

    def get_email(message_id: str) -> EmailDetail:
        calls["gmail"] += 1
        return fake_email()

    def analyse(email: EmailDetail):
        calls["model"] += 1
        return (
            fake_analysis(),
            ModelUsage(model=fake_analysis().model, latency_ms=1500, total_tokens=900),
            None,
        )

    monkeypatch.setattr(analyze_router.gmail_client, "get_email", get_email)
    monkeypatch.setattr(analyze_router.analyzer, "analyse", analyse)
    return calls


def test_first_call_is_not_cached(fake_pipeline: dict[str, int]) -> None:
    response = client.post("/analyze", json={"message_id": "msg-cache"})
    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is False
    assert body["analysis"]["message_id"] == "msg-cache"
    assert body["usage"]["latency_ms"] == 1500
    assert fake_pipeline == {"gmail": 1, "model": 1}


def test_second_call_served_from_cache(fake_pipeline: dict[str, int]) -> None:
    first = client.post("/analyze", json={"message_id": "msg-cache"})
    second = client.post("/analyze", json={"message_id": "msg-cache"})

    assert first.status_code == second.status_code == 200
    assert second.json()["cached"] is True
    # The expensive work ran once, for the first request only.
    assert fake_pipeline == {"gmail": 1, "model": 1}
    # The stored usage comes back too, so the UI can still show the measurement.
    assert second.json()["usage"]["latency_ms"] == 1500
    assert second.json()["analysis"] == first.json()["analysis"]


def test_force_refresh_bypasses_the_cache(fake_pipeline: dict[str, int]) -> None:
    client.post("/analyze", json={"message_id": "msg-cache"})
    refreshed = client.post(
        "/analyze", json={"message_id": "msg-cache", "force_refresh": True}
    )

    assert refreshed.status_code == 200
    assert refreshed.json()["cached"] is False
    assert fake_pipeline == {"gmail": 2, "model": 2}


def test_model_failure_is_not_cached(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services import store

    monkeypatch.setattr(analyze_router.gmail_client, "get_email", lambda _id: fake_email())

    def explode(_email: EmailDetail):
        raise UpstreamError("The model is unavailable", details={"status": 503})

    monkeypatch.setattr(analyze_router.analyzer, "analyse", explode)

    response = client.post("/analyze", json={"message_id": "msg-cache"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
    # A failure must leave no trace that a later request could mistake for a
    # real analysis.
    assert store.cache_lookup("msg-cache") is None


def test_unknown_email_returns_404_and_caches_nothing(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services import store

    def missing(_message_id: str) -> EmailDetail:
        raise NotFound("The requested email does not exist.")

    monkeypatch.setattr(analyze_router.gmail_client, "get_email", missing)

    response = client.post("/analyze", json={"message_id": "no-such-email"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert store.cache_lookup("no-such-email") is None


def test_cache_hit_skips_gmail_entirely(
    fake_pipeline: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful analysis, Gmail may go away and the demo still works."""
    client.post("/analyze", json={"message_id": "msg-cache"})

    def gmail_is_down(_message_id: str) -> EmailDetail:
        raise UpstreamError("Gmail is unreachable")

    monkeypatch.setattr(analyze_router.gmail_client, "get_email", gmail_is_down)

    response = client.post("/analyze", json={"message_id": "msg-cache"})
    assert response.status_code == 200
    assert response.json()["cached"] is True
