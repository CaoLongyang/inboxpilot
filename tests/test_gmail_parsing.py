"""Tests for the Gmail parsing layer.

These run without credentials: the parsing functions are pure, which is exactly
why they are separated from the API calls.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import pytest

from backend.errors import AppError, AuthError, ConfigError
from backend.services import gmail_client, token_store


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


@pytest.fixture
def no_credentials(monkeypatch):
    """Credential-free settings, so the suite never depends on a local .env."""
    from backend.config import Settings

    def fake_settings() -> Settings:
        return Settings(
            _env_file=None,
            google_client_id="",
            google_client_secret="",
            demo_refresh_token="",
        )

    monkeypatch.setattr(gmail_client, "get_settings", fake_settings)
    monkeypatch.setattr(token_store, "get_settings", fake_settings)


def test_html_to_text_drops_script_and_style() -> None:
    html = """
    <html><head><style>.a{color:red}</style></head>
    <body>
      <p>Registration opens on <b>March 3</b>.</p>
      <script>alert('x')</script>
      <div>Book a slot before March 1.</div>
    </body></html>
    """
    text = gmail_client.html_to_text(html)
    assert "Registration opens on March 3." in text
    assert "Book a slot before March 1." in text
    assert "alert" not in text
    assert "color:red" not in text


def test_decode_body_tolerates_missing_padding() -> None:
    assert gmail_client.decode_body(b64("hello world")) == "hello world"
    assert gmail_client.decode_body(None) == ""
    assert gmail_client.decode_body("!!!not-base64!!!") == ""


def test_extract_bodies_prefers_plain_text() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>html body</p>")}},
        ],
    }
    text, html = gmail_client.extract_bodies(payload)
    assert text == "plain body"
    assert html is not None and "html body" in html


def test_extract_bodies_falls_back_to_html() -> None:
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [{"mimeType": "text/html", "body": {"data": b64("<p>only html</p>")}}],
    }
    text, html = gmail_client.extract_bodies(payload)
    assert text == "only html"
    assert html is not None


def test_to_summary_maps_headers_and_unread() -> None:
    message = {
        "id": "18c1",
        "threadId": "18c0",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Please book a slot",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Academic advising"},
                {"name": "From", "value": "Prof. Tan <tan@uni.edu>"},
                {"name": "To", "value": "me@example.com, other@example.com"},
                {"name": "Date", "value": "Mon, 02 Mar 2026 09:30:00 +0800"},
            ]
        },
    }
    summary = gmail_client.to_summary(message)
    assert summary.id == "18c1"
    assert summary.subject == "Academic advising"
    assert summary.sender.startswith("Prof. Tan")
    assert summary.recipients == ["me@example.com", "other@example.com"]
    assert summary.unread is True
    assert summary.date is not None and summary.date.year == 2026


def test_to_detail_includes_body_and_keeps_from_alias() -> None:
    message = {
        "id": "1",
        "threadId": "1",
        "labelIds": [],
        "snippet": "",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "S"},
                {"name": "From", "value": "a@b.c"},
            ],
            "mimeType": "text/plain",
            "body": {"data": b64("body text")},
        },
    }
    detail = gmail_client.to_detail(message)
    assert detail.body_text == "body text"
    dumped = detail.model_dump(by_alias=True)
    assert dumped["from"] == "a@b.c"
    assert "sender" not in dumped


def test_draft_creation_requires_a_connected_mailbox() -> None:
    """Now that the write exists, the thing it must never do is run unconnected."""
    with pytest.raises(AuthError):
        gmail_client.create_draft(to=["a@b.c"], subject="s", body="b")


def test_draft_creation_rejects_a_header_injection_attempt() -> None:
    """A newline in a recipient is how a Bcc gets smuggled in."""
    with pytest.raises(AppError) as exc:
        gmail_client.create_draft(
            to=["a@b.c\r\nBcc: attacker@evil.test"], subject="s", body="b"
        )
    assert exc.value.code == "invalid_recipient"


def test_oauth_requires_configuration(no_credentials) -> None:
    with pytest.raises(ConfigError):
        gmail_client.get_authorization_url()


def test_auth_status_is_safe_without_credentials(no_credentials) -> None:
    status = gmail_client.connection_status()
    assert status["connected"] is False
    assert "refresh_token" not in status


def test_pkce_verifier_survives_until_exchange() -> None:
    """Regression: the verifier used to be thrown away with the state.

    google-auth-oauthlib autogenerates a PKCE verifier per Flow instance. The
    authorization URL is built by one Flow and the token exchange by another, so
    unless the verifier is persisted the exchange sends a mismatched verifier
    and Google answers `invalid_grant` on every single attempt.
    """
    if not gmail_client.is_configured():
        pytest.skip("Google OAuth client is not configured")

    url = gmail_client.get_authorization_url()
    params = parse_qs(urlparse(url).query)
    state = params["state"][0]

    verifier = gmail_client._PENDING_STATES[state]["code_verifier"]
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    assert params["code_challenge_method"][0] == "S256"
    assert params["code_challenge"][0] == expected
    # And it must still be there when the callback arrives.
    assert gmail_client._consume_state(state)["code_verifier"] == verifier
