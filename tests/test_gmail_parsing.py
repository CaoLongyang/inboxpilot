"""Tests for the Gmail parsing layer.

These run without credentials: the parsing functions are pure, which is exactly
why they are separated from the API calls.
"""

from __future__ import annotations

import base64

import pytest

from backend.errors import ConfigError, NotImplementedYet
from backend.services import gmail_client


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


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


def test_draft_creation_is_not_silently_available() -> None:
    with pytest.raises(NotImplementedYet):
        gmail_client.create_draft(to=["a@b.c"], subject="s", body="b")


def test_oauth_requires_configuration() -> None:
    with pytest.raises(ConfigError):
        gmail_client.get_authorization_url()


def test_auth_status_is_safe_without_credentials() -> None:
    status = gmail_client.connection_status()
    assert status["connected"] is False
    assert "refresh_token" not in status
