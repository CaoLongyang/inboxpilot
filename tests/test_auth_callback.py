"""The OAuth callback, which is the one endpoint a human sees in a browser.

Before this existed, a successful connection ended on a raw JSON blob and a
denied consent screen ended on a 422 validation error. Both are technically
correct and both look broken, which matters because this is the first thing a
reviewer clicks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.errors import AuthError, ConfigError
from backend.main import app
from backend.routers import auth as auth_router

client = TestClient(app)


def test_success_redirects_to_the_frontend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        auth_router.gmail_client,
        "exchange_code",
        lambda code, state: {"connected": True, "email": "me@example.com"},
    )

    response = client.get(
        "/auth/google/callback?code=abc&state=xyz", follow_redirects=False
    )
    assert response.status_code == 302
    assert response.headers["location"] == auth_router.get_settings().frontend_url
    # The token never travels through the browser.
    assert "refresh_token" not in response.text.lower()


def test_denied_consent_is_a_readable_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reviewer clicking "Cancel" on Google's screen lands here."""
    called = {"exchange": 0}

    def must_not_run(code, state):
        called["exchange"] += 1
        return {}

    monkeypatch.setattr(auth_router.gmail_client, "exchange_code", must_not_run)

    response = client.get("/auth/google/callback?error=access_denied")
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "not granted" in response.text.lower()
    assert "access_denied" in response.text
    assert called["exchange"] == 0  # nothing to exchange


def test_missing_code_is_a_readable_page() -> None:
    response = client.get("/auth/google/callback")
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "missing authorization code" in response.text.lower()


def test_exchange_failure_shows_the_code_and_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(code, state):
        raise AuthError("Google rejected the authorization code")

    monkeypatch.setattr(auth_router.gmail_client, "exchange_code", reject)

    response = client.get("/auth/google/callback?code=abc&state=xyz")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert "not_authenticated" in response.text
    assert "request id" in response.text.lower()
    assert response.headers["X-Request-ID"] in response.text


def test_unexpected_failure_does_not_leak_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(code, state):
        raise RuntimeError("internal detail that must not reach a browser")

    monkeypatch.setattr(auth_router.gmail_client, "exchange_code", explode)

    response = client.get("/auth/google/callback?code=abc&state=xyz")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "internal detail" not in response.text
    assert "Traceback" not in response.text


def test_configuration_error_keeps_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def unconfigured(code, state):
        raise ConfigError(
            "Google OAuth is not configured", details={"env_var": "GOOGLE_CLIENT_ID"}
        )

    monkeypatch.setattr(auth_router.gmail_client, "exchange_code", unconfigured)

    response = client.get("/auth/google/callback?code=abc&state=xyz")
    assert response.status_code == 500
    assert "configuration_error" in response.text
    assert "GOOGLE_CLIENT_ID" in response.text


def test_the_page_is_theme_neutral() -> None:
    """The browser page must not assume the app's theme."""
    response = client.get("/auth/google/callback")
    assert "prefers-color-scheme" in response.text
