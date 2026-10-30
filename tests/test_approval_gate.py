"""The approval gate (tasks A12 / A13).

Every test here is about a refusal that must happen before a byte reaches Gmail,
or about a claim we must not make after it does. The Gmail client is faked, so a
test that passes proves our logic, not Google's availability.
"""

from __future__ import annotations

import pytest

from backend.errors import (
    AppError,
    ApprovalRequired,
    AuthError,
    DuplicateRequest,
    NotFound,
    UpstreamError,
)
from backend.schemas import (
    ActionKind,
    DraftCreateRequest,
    DraftProposal,
    ExecutionStatus,
    ProposedAction,
)
from backend.services import drafter, gmail_client, store, token_store

ACCOUNT = "24211639@siswa.um.edu.my"


@pytest.fixture
def wired(monkeypatch, temp_db):
    """A connected mailbox, a stubbed Gmail write, and a clean database."""
    monkeypatch.setattr(
        token_store,
        "status",
        lambda: {"connected": True, "email": ACCOUNT, "scopes": [], "source": "file"},
    )
    calls = {"create": [], "get": []}

    def fake_create(**kwargs):
        calls["create"].append(kwargs)
        return "draft-abc"

    def fake_get(draft_id):
        calls["get"].append(draft_id)
        last = calls["create"][-1] if calls["create"] else {}
        return {
            "draft_id": draft_id,
            "message_id": "gmail-msg-1",
            "thread_id": last.get("thread_id"),
            "to": list(last.get("to") or []),
            "cc": list(last.get("cc") or []),
            "subject": last.get("subject", ""),
            "body_text": last.get("body", ""),
            "rfc_message_id": "<new@inboxpilot.local>",
        }

    monkeypatch.setattr(gmail_client, "create_draft", fake_create)
    monkeypatch.setattr(gmail_client, "get_draft", fake_get)
    return calls


def make_proposal(**overrides) -> ProposedAction:
    base = dict(
        id="prop-1",
        message_id="msg-1",
        kind=ActionKind.CREATE_DRAFT,
        title="Reply to the lecturer",
        why="The email asks for a file by a date.",
        missing_info=[],
        draft=DraftProposal(
            to=["chiam@um.edu.my"],
            subject="Re: Proposal Presentation",
            body="Terima kasih, saya telah memuat naik fail.",
            thread_id="thread-1",
            in_reply_to="<orig@mail.gmail.com>",
        ),
    )
    base.update(overrides)
    return ProposedAction(**base)


def make_request(**overrides) -> DraftCreateRequest:
    base = dict(
        proposal_id="prop-1",
        idempotency_key="key-0123456789ab",
        draft=DraftProposal(
            to=["chiam@um.edu.my"],
            subject="Re: Proposal Presentation",
            body="Terima kasih, saya telah memuat naik fail.",
            thread_id="thread-1",
            in_reply_to="<orig@mail.gmail.com>",
        ),
        approved_by=ACCOUNT,
        approved=True,
    )
    base.update(overrides)
    return DraftCreateRequest(**base)


# --------------------------------------------------------------------------
# Refusals: nothing reaches Gmail
# --------------------------------------------------------------------------


def test_unapproved_request_never_writes(wired) -> None:
    store.save_proposal(make_proposal())
    with pytest.raises(ApprovalRequired):
        drafter.execute(make_request(approved=False))
    assert wired["create"] == []


def test_unknown_proposal_is_not_an_approval(wired) -> None:
    """A proposal id the server cannot resolve is a client-side invention."""
    with pytest.raises(NotFound):
        drafter.execute(make_request(proposal_id="prop-does-not-exist"))
    assert wired["create"] == []


def test_a_non_draft_proposal_is_refused(wired) -> None:
    store.save_proposal(make_proposal(kind=ActionKind.NONE, draft=None))
    with pytest.raises(ApprovalRequired):
        drafter.execute(make_request())
    assert wired["create"] == []


def test_a_placeholder_in_the_approved_body_blocks_execution(wired) -> None:
    """The gate blocks on the unresolved text, not on the model's note.

    Blocking on `missing_info` would refuse forever: the note cannot be cleared
    by fixing the draft, so a user who filled the gap in correctly would still be
    told to start over.
    """
    store.save_proposal(make_proposal(missing_info=["confirm the submission date"]))
    request = make_request(
        draft=DraftProposal(
            to=["chiam@um.edu.my"],
            subject="Re: Proposal Presentation",
            body="I will submit by [confirm the submission date].",
            thread_id="thread-1",
        )
    )
    with pytest.raises(ApprovalRequired) as exc:
        drafter.execute(request)
    assert exc.value.details["placeholders"] == ["[confirm the submission date]"]
    assert wired["create"] == []


def test_filling_the_placeholder_unblocks_execution(wired) -> None:
    """The user's edit is what resolves the gap; nothing else needs regenerating."""
    store.save_proposal(make_proposal(missing_info=["confirm the submission date"]))
    request = make_request(
        draft=DraftProposal(
            to=["chiam@um.edu.my"],
            subject="Re: Proposal Presentation",
            body="I will submit by 24 October 2026.",
            thread_id="thread-1",
        )
    )
    result = drafter.execute(request)
    assert result.status is ExecutionStatus.CREATED
    assert len(wired["create"]) == 1


def test_ordinary_brackets_are_not_treated_as_placeholders(wired) -> None:
    """`[1]` style citations are content, not a gap; the pattern needs 3+ chars."""
    store.save_proposal(make_proposal())
    request = make_request(
        draft=DraftProposal(
            to=["chiam@um.edu.my"],
            subject="Re: Proposal Presentation",
            body="As noted in the rubric [1], the slides are ready.",
            thread_id="thread-1",
        )
    )
    assert drafter.execute(request).status is ExecutionStatus.CREATED


def test_the_approver_must_own_the_mailbox(wired) -> None:
    store.save_proposal(make_proposal())
    with pytest.raises(ApprovalRequired):
        drafter.execute(make_request(approved_by="someone.else@example.com"))
    assert wired["create"] == []


def test_approver_comparison_ignores_case_and_spacing(wired) -> None:
    store.save_proposal(make_proposal())
    result = drafter.execute(make_request(approved_by="  24211639@SISWA.um.edu.my "))
    assert result.status is ExecutionStatus.CREATED


def test_disconnected_mailbox_refuses_before_writing(wired, monkeypatch) -> None:
    store.save_proposal(make_proposal())
    monkeypatch.setattr(token_store, "status", lambda: {"connected": False})
    with pytest.raises(AuthError):
        drafter.execute(make_request())
    assert wired["create"] == []


# --------------------------------------------------------------------------
# The happy path, and what "verified" actually means
# --------------------------------------------------------------------------


def test_approved_request_creates_and_verifies_a_draft(wired) -> None:
    store.save_proposal(make_proposal())
    result = drafter.execute(make_request())

    assert result.status is ExecutionStatus.CREATED
    assert result.draft_id == "draft-abc"
    assert result.verified is True
    assert result.action_id == "prop-1"
    assert len(wired["create"]) == 1
    assert wired["get"] == ["draft-abc"], "success must be read back, not assumed"


def test_the_draft_uses_the_thread_the_user_approved(wired) -> None:
    store.save_proposal(make_proposal())
    drafter.execute(make_request())
    sent = wired["create"][0]
    assert sent["thread_id"] == "thread-1"
    assert sent["in_reply_to"] == "<orig@mail.gmail.com>"


def test_execution_is_recorded_and_queryable(wired) -> None:
    store.save_proposal(make_proposal())
    drafter.execute(make_request())

    stored = store.get_execution("prop-1")
    assert stored is not None
    assert stored.status is ExecutionStatus.CREATED
    assert stored.draft_id == "draft-abc"
    assert stored.verified is True


def test_a_readback_that_disagrees_is_not_verified(wired, monkeypatch) -> None:
    store.save_proposal(make_proposal())

    def tampered(draft_id):
        return {
            "draft_id": draft_id,
            "message_id": "gmail-msg-1",
            "thread_id": "thread-1",
            "to": ["chiam@um.edu.my"],
            "cc": [],
            "subject": "Something else entirely",
            "body_text": "Terima kasih, saya telah memuat naik fail.",
            "rfc_message_id": "<x@y>",
        }

    monkeypatch.setattr(gmail_client, "get_draft", tampered)
    result = drafter.execute(make_request())

    assert result.status is ExecutionStatus.CREATED
    assert result.verified is False
    assert "subject" in result.message.lower()


def test_a_wrong_recipient_is_not_verified(wired, monkeypatch) -> None:
    store.save_proposal(make_proposal())
    monkeypatch.setattr(
        gmail_client,
        "get_draft",
        lambda draft_id: {
            "draft_id": draft_id,
            "message_id": "m",
            "thread_id": "t",
            "to": ["attacker@evil.test"],
            "cc": [],
            "subject": "Re: Proposal Presentation",
            "body_text": "Terima kasih, saya telah memuat naik fail.",
            "rfc_message_id": "<x@y>",
        },
    )
    result = drafter.execute(make_request())
    assert result.verified is False
    assert "recipients" in result.message.lower()


def test_body_comparison_tolerates_wrapping_but_not_edits(wired, monkeypatch) -> None:
    """Gmail re-wraps long lines; that must not read as a mismatch."""
    store.save_proposal(make_proposal())
    body = "Terima kasih, saya telah memuat naik fail."
    monkeypatch.setattr(
        gmail_client,
        "get_draft",
        lambda draft_id: {
            "draft_id": draft_id,
            "message_id": "m",
            "thread_id": "t",
            "to": ["chiam@um.edu.my"],
            "cc": [],
            "subject": "Re: Proposal Presentation",
            "body_text": "Terima kasih,\n   saya telah memuat\nnaik fail.",
            "rfc_message_id": "<x@y>",
        },
    )
    assert drafter.execute(make_request()).verified is True

    monkeypatch.setattr(
        gmail_client,
        "get_draft",
        lambda draft_id: {
            "draft_id": draft_id,
            "message_id": "m",
            "thread_id": "t",
            "to": ["chiam@um.edu.my"],
            "cc": [],
            "subject": "Re: Proposal Presentation",
            "body_text": "A completely different message.",
            "rfc_message_id": "<x@y>",
        },
    )
    second = drafter.execute(make_request(idempotency_key="key-ffffffffffff"))
    assert second.verified is False
    assert second.status is ExecutionStatus.CREATED


def test_created_but_unreadable_is_reported_honestly(wired, monkeypatch) -> None:
    store.save_proposal(make_proposal())

    def unreadable(_draft_id):
        raise UpstreamError("Gmail is unreachable")

    monkeypatch.setattr(gmail_client, "get_draft", unreadable)
    result = drafter.execute(make_request())

    assert result.status is ExecutionStatus.CREATED
    assert result.verified is False
    assert "read" in result.message.lower()


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_repeating_an_approval_does_not_create_a_second_draft(wired) -> None:
    store.save_proposal(make_proposal())
    first = drafter.execute(make_request())
    second = drafter.execute(make_request())

    assert len(wired["create"]) == 1, "the second call must not reach Gmail"
    assert first.draft_id == second.draft_id


def test_a_different_key_creates_its_own_draft(wired) -> None:
    """Two genuinely separate approvals are two drafts, not a replay."""
    store.save_proposal(make_proposal())
    drafter.execute(make_request())
    drafter.execute(make_request(idempotency_key="key-bbbbbbbbbbbb"))
    assert len(wired["create"]) == 2


def test_an_unfinished_attempt_refuses_a_retry(wired) -> None:
    """A `pending` row means the outcome is unknown; retrying could duplicate."""
    store.save_proposal(make_proposal())
    store.record_execution(
        drafter.ExecutionResult(
            action_id="prop-1",
            status=ExecutionStatus.PENDING,
            message="Draft creation in progress",
        ),
        idempotency_key="key-0123456789ab",
        proposal_id="prop-1",
        message_id="msg-1",
    )
    with pytest.raises(DuplicateRequest):
        drafter.execute(make_request())
    assert wired["create"] == []


def test_a_failed_write_is_recorded_and_not_retried(wired, monkeypatch) -> None:
    store.save_proposal(make_proposal())

    def failing(**_kwargs):
        raise UpstreamError("Gmail said no")

    monkeypatch.setattr(gmail_client, "create_draft", failing)
    with pytest.raises(UpstreamError):
        drafter.execute(make_request())

    stored = store.get_execution("prop-1")
    assert stored is not None
    assert stored.status is ExecutionStatus.FAILED

    # The key is burned: a retry replays the failure instead of risking a second
    # draft whose existence we could not confirm.
    replay = drafter.execute(make_request())
    assert replay.status is ExecutionStatus.FAILED
    assert len(wired["create"]) == 0


def test_a_lost_race_replays_the_winner(wired, monkeypatch) -> None:
    """Two concurrent identical approvals: one writes, the other replays."""
    store.save_proposal(make_proposal())
    original = store.record_execution

    def losing_race(result, **kwargs):
        # The other thread got the key first.
        original(result, **kwargs)
        raise DuplicateRequest("already claimed")

    monkeypatch.setattr(store, "record_execution", losing_race)
    monkeypatch.setattr(
        store,
        "find_execution_by_key",
        lambda key: drafter.ExecutionResult(
            action_id="prop-1",
            status=ExecutionStatus.CREATED,
            draft_id="draft-from-winner",
            verified=True,
            message="ok",
        ),
    )
    result = drafter.execute(make_request())
    assert result.draft_id == "draft-from-winner"
    assert wired["create"] == []
