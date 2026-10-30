"""Tests for the draft-writing tool (task A11).

No network and no credentials: the Gmail service is faked at the boundary, so
what is under test is our MIME construction and our request shape. Both are
places where a mistake is invisible until a human opens the draft in Gmail and
sees mojibake or a missing thread link.
"""

from __future__ import annotations

import base64
import email
from email import message_from_bytes
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from backend.errors import AppError, AuthError, UpstreamError
from backend.services import gmail_client


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


class _Executable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeDrafts:
    def __init__(self, *, create_result=None, get_result=None):
        self.create_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self._create_result = create_result if create_result is not None else {
            "id": "draft-1",
            "message": {"id": "msg-1", "threadId": "thread-1"},
        }
        self._get_result = get_result

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return _Executable(self._create_result)

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return _Executable(self._get_result)


def install(monkeypatch, drafts: FakeDrafts) -> FakeDrafts:
    service = SimpleNamespace(users=lambda: SimpleNamespace(drafts=lambda: drafts))
    monkeypatch.setattr(gmail_client, "_service", lambda: service)
    return drafts


def decode_raw(request: dict) -> email.message.Message:
    raw = request["message"]["raw"]
    padded = raw + "=" * (-len(raw) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(padded.encode("ascii")))


def decoded_subject(message: email.message.Message) -> str:
    from email.header import decode_header, make_header

    return str(make_header(decode_header(message["Subject"])))


# --------------------------------------------------------------------------
# MIME construction
# --------------------------------------------------------------------------


def test_mime_keeps_a_non_ascii_subject_readable() -> None:
    """The demo mailbox is Malaysian; accents and CJK must survive the round trip."""
    raw = gmail_client.build_mime(
        to=["chiam@um.edu.my"], subject="Re: 截止日期 — 24 Oct", body="Terima kasih"
    )
    message = message_from_bytes(raw)
    assert decoded_subject(message) == "Re: 截止日期 — 24 Oct"
    # Encoded as RFC 2047, not raw 8-bit in the header.
    assert "=?" in message["Subject"]


def test_mime_keeps_a_non_ascii_body_intact() -> None:
    body = "Salam,\n\nSaya telah memuat naik fail. 谢谢。\n"
    raw = gmail_client.build_mime(to=["a@b.co"], subject="s", body=body)
    message = message_from_bytes(raw)
    assert message.get_content_charset() == "utf-8"
    assert message.get_payload(decode=True).decode("utf-8") == body


def test_mime_sets_the_threading_headers() -> None:
    raw = gmail_client.build_mime(
        to=["a@b.co"],
        subject="Re: hi",
        body="ok",
        in_reply_to="<orig@mail.gmail.com>",
    )
    message = message_from_bytes(raw)
    assert message["In-Reply-To"] == "<orig@mail.gmail.com>"
    # Without References, Gmail still starts a new conversation in some clients.
    assert message["References"] == "<orig@mail.gmail.com>"


def test_mime_always_carries_its_own_message_id() -> None:
    raw = gmail_client.build_mime(to=["a@b.co"], subject="s", body="b")
    assert "@" in message_from_bytes(raw)["Message-ID"]


def test_mime_includes_cc_only_when_present() -> None:
    without = message_from_bytes(
        gmail_client.build_mime(to=["a@b.co"], subject="s", body="b")
    )
    with_cc = message_from_bytes(
        gmail_client.build_mime(to=["a@b.co"], cc=["c@d.co"], subject="s", body="b")
    )
    assert without["Cc"] is None
    assert with_cc["Cc"] == "c@d.co"


# --------------------------------------------------------------------------
# The request we send
# --------------------------------------------------------------------------


def test_create_draft_sends_base64url_and_the_thread_id(monkeypatch) -> None:
    drafts = install(monkeypatch, FakeDrafts())
    draft_id = gmail_client.create_draft(
        to=["a@b.co"], subject="Re: hi", body="ok", thread_id="thread-9"
    )
    assert draft_id == "draft-1"
    sent = drafts.create_calls[0]
    assert sent["userId"] == "me"
    assert sent["body"]["message"]["threadId"] == "thread-9"
    # urlsafe base64: no '+' or '/' that would need escaping in a URL.
    assert "+" not in sent["body"]["message"]["raw"]
    assert "/" not in sent["body"]["message"]["raw"]


def test_create_draft_omits_thread_id_when_there_is_none(monkeypatch) -> None:
    drafts = install(monkeypatch, FakeDrafts())
    gmail_client.create_draft(to=["a@b.co"], subject="s", body="b")
    assert "threadId" not in drafts.create_calls[0]["body"]["message"]


def test_create_draft_fails_loudly_when_gmail_returns_no_id(monkeypatch) -> None:
    install(monkeypatch, FakeDrafts(create_result={"message": {}}))
    with pytest.raises(UpstreamError):
        gmail_client.create_draft(to=["a@b.co"], subject="s", body="b")


def test_create_draft_maps_a_permission_error(monkeypatch) -> None:
    error = HttpError(SimpleNamespace(status=403, reason="Forbidden"), b"denied")
    install(monkeypatch, FakeDrafts(create_result=error))
    with pytest.raises(UpstreamError) as exc:
        gmail_client.create_draft(to=["a@b.co"], subject="s", body="b")
    assert exc.value.details["status"] == 403


# --------------------------------------------------------------------------
# Input validation: the write must refuse before it reaches Gmail
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipient",
    [
        "not-an-address",
        "a@b.co\r\nBcc: attacker@evil.test",
        "a@b.co\nBcc: attacker@evil.test",
        "two@at@signs.co",
        "   ",
    ],
)
def test_create_draft_rejects_a_bad_recipient(monkeypatch, recipient: str) -> None:
    drafts = install(monkeypatch, FakeDrafts())
    with pytest.raises(AppError) as exc:
        gmail_client.create_draft(to=[recipient], subject="s", body="b")
    assert exc.value.code == "invalid_recipient"
    assert drafts.create_calls == [], "Gmail must not be called for invalid input"


def test_create_draft_requires_a_subject_and_a_body(monkeypatch) -> None:
    drafts = install(monkeypatch, FakeDrafts())
    with pytest.raises(AppError):
        gmail_client.create_draft(to=["a@b.co"], subject="  ", body="b")
    with pytest.raises(AppError):
        gmail_client.create_draft(to=["a@b.co"], subject="s", body="   ")
    assert drafts.create_calls == []


def test_validation_runs_before_the_service_is_built(monkeypatch) -> None:
    """Ordering matters: bad input must not even open a connection to Google."""
    called = {"service": False}

    def forbidden():
        called["service"] = True
        raise AssertionError("the Gmail service was built for invalid input")

    monkeypatch.setattr(gmail_client, "_service", forbidden)
    with pytest.raises(AppError) as exc:
        gmail_client.create_draft(to=["not-an-address"], subject="s", body="b")
    assert exc.value.code == "invalid_recipient"
    assert called["service"] is False


# --------------------------------------------------------------------------
# Read-back
# --------------------------------------------------------------------------


def test_get_draft_flattens_the_payload(monkeypatch) -> None:
    payload = {
        "id": "draft-1",
        "message": {
            "id": "msg-1",
            "threadId": "thread-1",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "To", "value": "Dr. Chiam <chiam@um.edu.my>"},
                    {"name": "Subject", "value": "Re: Proposal"},
                    {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
                ],
                "body": {"data": b64("Terima kasih")},
            },
        },
    }
    install(monkeypatch, FakeDrafts(get_result=payload))

    result = gmail_client.get_draft("draft-1")
    assert result["draft_id"] == "draft-1"
    assert result["message_id"] == "msg-1"
    assert result["thread_id"] == "thread-1"
    assert result["to"] == ["Dr. Chiam <chiam@um.edu.my>"]
    assert result["subject"] == "Re: Proposal"
    assert result["body_text"] == "Terima kasih"
    assert result["rfc_message_id"] == "<abc@mail.gmail.com>"
