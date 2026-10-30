"""Task A10: repeated queries must never trigger a write.

The scary failure mode for this project is not a crash, it is a draft appearing
in a real mailbox because a page was refreshed. Three layers of defence, one per
test group:

1. a tripwire Gmail service that raises on any method except `list`, `get` and
   `threads.get`, so a read path that drifts into a write fails immediately;
2. `create_draft` itself is wrapped, so even a correct-looking call is caught;
3. a source-level check that `create_draft` is the *only* write primitive in the
   Gmail client — adding `send_message` later breaks the suite until someone
   removes or justifies it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.routers import analyze as analyze_router
from backend.schemas import EmailAnalysis, EmailCategory, ModelUsage
from backend.services import gmail_client
from tests.test_gmail_api import GmailFake, make_message

client = TestClient(app)

# Anything containing one of these substrings would be a write primitive.
WRITE_VERBS = ("send", "delete", "trash", "modify", "archive", "insert", "batch")


# --------------------------------------------------------------------------
# Tripwires
# --------------------------------------------------------------------------


class _ReadOnly:
    """Delegate the read methods; raise on everything else."""

    def __init__(self, inner, allowed: set[str]) -> None:
        self._inner = inner
        self._allowed = allowed

    def __getattr__(self, name: str):
        if name not in self._allowed:
            raise AssertionError(
                f"a read path called a write method: {name}() - "
                "reads must never mutate the mailbox"
            )
        return getattr(self._inner, name)


class TripwireService:
    def __init__(self, inner: GmailFake) -> None:
        self._inner = inner

    def users(self) -> "TripwireService":
        return self

    def messages(self) -> _ReadOnly:
        return _ReadOnly(self._inner.messages(), {"list", "get"})

    def threads(self) -> _ReadOnly:
        return _ReadOnly(self._inner.threads(), {"get"})

    def drafts(self):
        raise AssertionError("a read path reached drafts(): that is a write")


@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> GmailFake:
    """Fake Gmail behind a tripwire, plus a tripwire on create_draft."""
    fake = GmailFake()
    message = make_message("m1", subject="Hello", thread_id="t1")
    fake.by_id["m1"] = message
    fake.threads_by_id["t1"] = [message]
    fake.list_ids = ["m1"]

    monkeypatch.setattr(gmail_client, "_service", lambda: TripwireService(fake))

    def forbidden_create_draft(**_kwargs):
        raise AssertionError("create_draft was called by a non-write path")

    monkeypatch.setattr(gmail_client, "create_draft", forbidden_create_draft)
    return fake


@pytest.fixture
def fake_model(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"model": 0}

    def analyse(_email):
        calls["model"] += 1
        return (
            EmailAnalysis(
                message_id="m1",
                thread_id="t1",
                summary="A short note.",
                category=EmailCategory.INFORMATION_ONLY,
                action_required=False,
                model="nvidia/nemotron-test",
            ),
            ModelUsage(model="nvidia/nemotron-test", latency_ms=42, total_tokens=10),
            None,
        )

    monkeypatch.setattr(analyze_router.analyzer, "analyse", analyse)
    return calls


# --------------------------------------------------------------------------
# 1. Every read endpoint stays read-only
# --------------------------------------------------------------------------


def test_read_endpoints_never_touch_a_write_method(guarded: GmailFake) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/auth/status").status_code == 200
    assert client.get("/emails").status_code == 200
    assert client.get("/emails/m1").status_code == 200
    assert client.get("/emails/m1/thread").status_code == 200


def test_polling_the_inbox_is_harmless(guarded: GmailFake) -> None:
    """The frontend re-renders on every interaction; that must be free."""
    for _ in range(20):
        assert client.get("/emails").status_code == 200
        assert client.get("/emails/m1").status_code == 200


def test_repeated_analyze_runs_the_model_once_and_writes_nothing(
    guarded: GmailFake, fake_model: dict[str, int]
) -> None:
    for _ in range(5):
        assert client.post("/analyze", json={"message_id": "m1"}).status_code == 200
    # One inference call for five clicks, and no Gmail write anywhere.
    assert fake_model["model"] == 1


def test_repeated_analyze_with_force_refresh_still_writes_nothing(
    guarded: GmailFake, fake_model: dict[str, int]
) -> None:
    for _ in range(3):
        assert (
            client.post(
                "/analyze", json={"message_id": "m1", "force_refresh": True}
            ).status_code
            == 200
        )
    assert fake_model["model"] == 3


# --------------------------------------------------------------------------
# 2. The tripwire itself is not a no-op
# --------------------------------------------------------------------------


def test_the_tripwire_actually_trips(guarded: GmailFake) -> None:
    """A guard that never fires is indistinguishable from no guard."""
    service = TripwireService(guarded)
    with pytest.raises(AssertionError):
        service.drafts()
    with pytest.raises(AssertionError):
        service.messages().modify(userId="me", id="m1")
    with pytest.raises(AssertionError):
        service.threads().delete(userId="me", id="t1")
    # ...while the read methods still work.
    assert service.messages().get(userId="me", id="m1").execute()["id"] == "m1"


# --------------------------------------------------------------------------
# 3. Source-level guard
# --------------------------------------------------------------------------


def test_create_draft_is_the_only_write_primitive() -> None:
    """If someone adds `send_message`, this fails on purpose.

    The product promise is "it never sends email". A helper called `send` that
    nothing calls today is still a loaded gun, so the test is about the name
    existing at all, not about it being called.
    """
    offenders = [
        name
        for name in dir(gmail_client)
        if not name.startswith("__")
        and any(verb in name.lower() for verb in WRITE_VERBS)
    ]
    assert offenders == [], f"unexpected write-shaped helpers: {offenders}"


def test_the_client_never_calls_send() -> None:
    """The product promise is "it never sends email", checked at the source.

    A helper named `send` would be caught by the test above, but the API call
    itself is made inline (`service.users().messages().send(...)`), so the name
    scan alone does not cover it. This does.
    """
    source = Path(gmail_client.__file__).read_text(encoding="utf-8")
    assert ".send(" not in source
    assert "messages().send" not in source


def test_draft_creation_is_the_only_write_path() -> None:
    """`create_draft` refuses before Gmail when the mailbox is not connected."""
    from backend.errors import AuthError

    with pytest.raises(AuthError):
        gmail_client.create_draft(to=["a@example.com"], subject="s", body="b")
