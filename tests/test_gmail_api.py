"""Task A7: interface tests for the Gmail read path.

Covers the matrix the plan asks for — a normal email, an email with no text
body, a multi-message thread, a missing message, a revoked credential, a missing
scope and a rate limit — at both the service layer and the HTTP layer.

The Gmail transport is faked; nothing here touches the network.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from backend.config import Settings
from backend.errors import AuthError, NotFound, UpstreamError
from backend.main import app
from backend.services import gmail_client

client = TestClient(app)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class FakeResponse:
    def __init__(self, status: int, reason: str = "error") -> None:
        self.status = status
        self.reason = reason


def http_error(status: int) -> HttpError:
    return HttpError(FakeResponse(status), b'{"error": {"message": "boom"}}')


class FakeRequest:
    def __init__(self, run) -> None:
        self._run = run

    def execute(self):
        return self._run()


def make_message(
    message_id: str,
    *,
    subject: str = "Subject",
    sender: str = "Sender <sender@example.com>",
    body: str | None = "Body text",
    html: str | None = None,
    thread_id: str = "thread-1",
    labels: list[str] | None = None,
) -> dict:
    parts = []
    if body is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(body)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})

    payload: dict = {
        "headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": sender},
            {"name": "To", "value": "me@example.com"},
            {"name": "Date", "value": "Mon, 02 Mar 2026 09:30:00 +0800"},
        ],
        "mimeType": "multipart/alternative" if len(parts) > 1 else "text/plain",
    }
    if len(parts) > 1:
        payload["parts"] = parts
    elif parts:
        payload["body"] = parts[0]["body"]
        payload["mimeType"] = parts[0]["mimeType"]

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": labels if labels is not None else ["INBOX"],
        "snippet": (body or "")[:40],
        "payload": payload,
    }


class _Messages:
    def __init__(self, owner: "GmailFake") -> None:
        self.o = owner

    def list(self, **kwargs):
        self.o.list_kwargs = kwargs

        def run():
            if self.o.list_error:
                raise self.o.list_error
            return {"messages": [{"id": i} for i in self.o.list_ids]}

        return FakeRequest(run)

    def get(self, **kwargs):
        message_id = kwargs.get("id")

        def run():
            if message_id in self.o.get_errors:
                raise self.o.get_errors[message_id]
            if message_id not in self.o.by_id:
                raise http_error(404)
            return self.o.by_id[message_id]

        return FakeRequest(run)


class _Threads:
    def __init__(self, owner: "GmailFake") -> None:
        self.o = owner

    def get(self, **kwargs):
        thread_id = kwargs.get("id")

        def run():
            if thread_id in self.o.thread_errors:
                raise self.o.thread_errors[thread_id]
            if thread_id not in self.o.threads_by_id:
                raise http_error(404)
            return {"messages": self.o.threads_by_id[thread_id]}

        return FakeRequest(run)


class GmailFake:
    """Mirrors the googleapiclient call shape: users().messages().list(...)."""

    def __init__(self) -> None:
        self.by_id: dict[str, dict] = {}
        self.threads_by_id: dict[str, list[dict]] = {}
        self.list_ids: list[str] = []
        self.list_error: HttpError | None = None
        self.get_errors: dict[str, HttpError] = {}
        self.thread_errors: dict[str, HttpError] = {}
        self.list_kwargs: dict = {}
        self._messages = _Messages(self)
        self._threads = _Threads(self)

    def users(self) -> "GmailFake":
        return self

    def messages(self) -> _Messages:
        return self._messages

    def threads(self) -> _Threads:
        return self._threads


@pytest.fixture
def gmail(monkeypatch) -> GmailFake:
    """Install a fake Gmail service and deterministic settings."""
    fake = GmailFake()
    monkeypatch.setattr(gmail_client, "_service", lambda: fake)
    monkeypatch.setattr(
        gmail_client,
        "get_settings",
        lambda: Settings(_env_file=None, max_emails_per_request=20),
    )
    return fake


# --------------------------------------------------------------------------
# Service layer (A5)
# --------------------------------------------------------------------------


def test_list_returns_summaries(gmail: GmailFake) -> None:
    gmail.list_ids = ["m1", "m2"]
    gmail.by_id = {
        "m1": make_message("m1", subject="First"),
        "m2": make_message("m2", subject="Second", labels=["INBOX", "UNREAD"]),
    }
    emails = gmail_client.list_emails(max_results=5)
    assert [e.subject for e in emails] == ["First", "Second"]
    assert emails[1].unread is True


def test_list_passes_the_search_query(gmail: GmailFake) -> None:
    gmail.list_ids = []
    gmail_client.list_emails(max_results=3, query="newer_than:7d")
    assert gmail.list_kwargs["q"] == "newer_than:7d"
    assert gmail.list_kwargs["maxResults"] == 3


def test_list_clamps_max_results_to_the_configured_limit(gmail: GmailFake) -> None:
    gmail.list_ids = []
    gmail_client.list_emails(max_results=500)
    assert gmail.list_kwargs["maxResults"] == 20


def test_list_clamps_a_zero_or_negative_limit(gmail: GmailFake) -> None:
    gmail.list_ids = []
    gmail_client.list_emails(max_results=0)
    assert gmail.list_kwargs["maxResults"] == 1


def test_one_unreadable_message_does_not_break_the_inbox(gmail: GmailFake) -> None:
    gmail.list_ids = ["m1", "broken", "m3"]
    gmail.by_id = {"m1": make_message("m1"), "m3": make_message("m3")}
    gmail.get_errors["broken"] = http_error(500)

    emails = gmail_client.list_emails(max_results=5)
    assert [e.id for e in emails] == ["m1", "m3"]


def test_message_with_no_text_body_falls_back_to_html(gmail: GmailFake) -> None:
    gmail.by_id["m1"] = make_message("m1", body=None, html="<p>HTML only</p>")
    detail = gmail_client.get_email("m1")
    assert detail.body_text == "HTML only"


def test_message_with_no_body_at_all_is_empty_not_an_error(gmail: GmailFake) -> None:
    gmail.by_id["m1"] = make_message("m1", body=None, html=None)
    detail = gmail_client.get_email("m1")
    assert detail.body_text == ""
    assert detail.body_html is None


def test_thread_returns_every_message(gmail: GmailFake) -> None:
    gmail.threads_by_id["t1"] = [
        make_message("m1", subject="Question", thread_id="t1"),
        make_message("m2", subject="Re: Question", thread_id="t1"),
        make_message("m3", subject="Re: Question", thread_id="t1"),
    ]
    thread = gmail_client.get_thread("t1")
    assert [m.id for m in thread] == ["m1", "m2", "m3"]


# --------------------------------------------------------------------------
# Error mapping (A7)
# --------------------------------------------------------------------------


def test_missing_message_maps_to_not_found(gmail: GmailFake) -> None:
    with pytest.raises(NotFound):
        gmail_client.get_email("nope")


def test_missing_thread_maps_to_not_found(gmail: GmailFake) -> None:
    with pytest.raises(NotFound):
        gmail_client.get_thread("nope")


def test_malformed_message_id_maps_to_not_found(gmail: GmailFake) -> None:
    """Gmail answers 400, not 404, for an id it cannot parse."""
    gmail.get_errors["bad-id"] = http_error(400)
    with pytest.raises(NotFound):
        gmail_client.get_email("bad-id")


def test_bad_search_query_is_not_reported_as_missing_email(gmail: GmailFake) -> None:
    gmail.list_error = http_error(400)
    with pytest.raises(UpstreamError):
        gmail_client.list_emails(max_results=5, query="in:not-a-folder")


def test_revoked_credential_maps_to_auth_error(gmail: GmailFake) -> None:
    gmail.get_errors["m1"] = http_error(401)
    with pytest.raises(AuthError):
        gmail_client.get_email("m1")


def test_missing_scope_maps_to_upstream_error(gmail: GmailFake) -> None:
    gmail.get_errors["m1"] = http_error(403)
    with pytest.raises(UpstreamError) as exc:
        gmail_client.get_email("m1")
    assert exc.value.details["status"] == 403


def test_rate_limit_maps_to_upstream_error(gmail: GmailFake) -> None:
    gmail.list_error = http_error(429)
    with pytest.raises(UpstreamError) as exc:
        gmail_client.list_emails(max_results=5)
    assert exc.value.details["status"] == 429


def test_unexpected_status_maps_to_upstream_error(gmail: GmailFake) -> None:
    gmail.list_error = http_error(500)
    with pytest.raises(UpstreamError) as exc:
        gmail_client.list_emails(max_results=5)
    assert exc.value.details["status"] == 500


def test_no_stored_credential_raises_auth_error(monkeypatch) -> None:
    monkeypatch.setattr(gmail_client.token_store, "load", lambda: None)
    with pytest.raises(AuthError) as exc:
        gmail_client._credentials()
    assert exc.value.code == "not_authenticated"


# --------------------------------------------------------------------------
# HTTP layer (A6)
# --------------------------------------------------------------------------


def test_get_emails_endpoint_returns_the_envelope(gmail: GmailFake) -> None:
    gmail.list_ids = ["m1"]
    gmail.by_id = {"m1": make_message("m1", subject="Hello")}

    response = client.get("/emails", params={"max_results": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["emails"][0]["subject"] == "Hello"
    assert body["emails"][0]["from"].startswith("Sender")


def test_get_email_endpoint_returns_a_detail(gmail: GmailFake) -> None:
    """Regression: this endpoint used to 500 on `EmailDetail(**model)`."""
    gmail.by_id["m1"] = make_message("m1", subject="Detail", body="Full body")

    response = client.get("/emails/m1")
    assert response.status_code == 200
    body = response.json()
    assert body["subject"] == "Detail"
    assert body["body_text"] == "Full body"


def test_thread_endpoint_returns_a_list(gmail: GmailFake) -> None:
    gmail.by_id["m2"] = make_message("m2", thread_id="t1")
    gmail.threads_by_id["t1"] = [
        make_message("m1", thread_id="t1"),
        make_message("m2", thread_id="t1"),
    ]

    response = client.get("/emails/m2/thread")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_missing_email_endpoint_uses_the_error_envelope(gmail: GmailFake) -> None:
    response = client.get("/emails/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


def test_revoked_credential_endpoint_uses_the_error_envelope(gmail: GmailFake) -> None:
    gmail.get_errors["m1"] = http_error(401)
    response = client.get("/emails/m1")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_upstream_failure_endpoint_uses_the_error_envelope(gmail: GmailFake) -> None:
    gmail.list_error = http_error(429)
    response = client.get("/emails")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"
