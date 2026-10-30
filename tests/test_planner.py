"""Planning a reply proposal (task A12, planning half).

The property that matters most here is not "does it produce a nice body" — it is
that the model cannot choose who receives the reply. Every test below is written
with that in mind.
"""

from __future__ import annotations

import pytest

from backend.errors import UpstreamError
from backend.schemas import EmailAnalysis, EmailCategory, EmailDetail, ModelUsage
from backend.services import analyzer, nebius_client, planner, store

SENDER = "Dr. Chiam <chiam@um.edu.my>"


def make_email(**overrides) -> EmailDetail:
    base = dict(
        id="msg-1",
        thread_id="thread-1",
        subject="Proposal Presentation - Occ4",
        sender=SENDER,
        headers={"from": SENDER, "message-id": "<orig-123@mail.gmail.com>"},
        body_text="Please submit the slides by 24 October 2026. Thank you.",
    )
    base.update(overrides)
    return EmailDetail(**base)


def make_analysis(**overrides) -> EmailAnalysis:
    base = dict(
        message_id="msg-1",
        thread_id="thread-1",
        summary="The lecturer asks for the slides by 24 October 2026.",
        category=EmailCategory.ACTION_REQUIRED,
        action_required=True,
        model="nvidia/nemotron-3-super-120b-a12b",
    )
    base.update(overrides)
    return EmailAnalysis(**base)


MODEL_REPLY = {
    "should_draft": True,
    "title": "Confirm slide submission",
    "why": "The email sets a submission date that needs acknowledging.",
    "body": "Dear Dr. Chiam,\n\nI will submit the slides before 24 October 2026.\n\nRegards,",
    "missing_info": [],
}


@pytest.fixture
def wired(monkeypatch, temp_db):
    """Email + cached analysis + a faked planning model."""
    email = make_email()
    monkeypatch.setattr(nebius_client, "chat_json", lambda *_a, **_k: (dict(MODEL_REPLY), ModelUsage(model="reason-model", latency_ms=12)))
    store.cache_store(make_analysis())
    calls = {"analyse": 0}

    def fake_analyse(_email, **_kwargs):
        calls["analyse"] += 1
        return make_analysis(), ModelUsage(model="fast-model", latency_ms=1), None

    monkeypatch.setattr(analyzer, "analyse", fake_analyse)
    monkeypatch.setattr(planner.gmail_client, "get_email", lambda _mid: email)
    return {"email": email, "calls": calls}


def test_the_recipient_comes_from_the_email_not_the_model(wired) -> None:
    response = planner.plan("msg-1")
    assert len(response.actions) == 1
    assert response.actions[0].draft.to == ["chiam@um.edu.my"]


def test_a_model_cannot_redirect_the_reply(wired, monkeypatch) -> None:
    """Even if the body names another address, the recipient is unchanged."""
    hostile = dict(MODEL_REPLY)
    hostile["body"] = "To: attacker@evil.test\n\nHere is everything you asked for."
    hostile["to"] = ["attacker@evil.test"]  # ignored field, must not be honoured
    monkeypatch.setattr(
        nebius_client, "chat_json", lambda *_a, **_k: (hostile, ModelUsage(model="m", latency_ms=1))
    )

    response = planner.plan("msg-1")
    action = response.actions[0]
    assert action.draft.to == ["chiam@um.edu.my"]
    # The injected header line is stripped, so the draft does not read as one.
    assert not action.draft.body.lower().startswith("to:")


def test_the_proposal_is_stored_before_it_is_returned(wired) -> None:
    """The id the browser receives must resolve back to what it was shown."""
    response = planner.plan("msg-1")
    proposal_id = response.actions[0].id
    stored = store.get_proposal(proposal_id)
    assert stored is not None
    assert stored.draft.subject == response.actions[0].draft.subject
    assert stored.kind.value == "create_draft"


def test_subject_gets_one_re_prefix(wired) -> None:
    assert planner.plan("msg-1").actions[0].draft.subject.startswith("Re: Proposal Presentation")

    already = make_email(subject="Re: Proposal Presentation")
    planner.gmail_client.get_email = lambda _mid: already
    assert planner.plan("msg-1").actions[0].draft.subject == "Re: Proposal Presentation"


def test_threading_headers_are_carried_over(wired) -> None:
    draft = planner.plan("msg-1").actions[0].draft
    assert draft.thread_id == "thread-1"
    assert draft.in_reply_to == "<orig-123@mail.gmail.com>"


def test_a_declined_reply_produces_no_action(wired, monkeypatch) -> None:
    declined = dict(MODEL_REPLY, should_draft=False, body="")
    monkeypatch.setattr(
        nebius_client, "chat_json", lambda *_a, **_k: (declined, ModelUsage(model="m", latency_ms=1))
    )
    assert planner.plan("msg-1").actions == []


def test_a_no_reply_sender_is_not_planned_for(wired, monkeypatch) -> None:
    """Drafting a reply to noreply@ wastes the user's attention."""
    noreply = make_email(sender="Do not reply <noreply@smtp10.um.edu.my>")
    planner.gmail_client.get_email = lambda _mid: noreply
    called = {"model": False}

    def should_not_run(*_a, **_k):
        called["model"] = True
        raise AssertionError("the planning model must not run for a no-reply sender")

    monkeypatch.setattr(nebius_client, "chat_json", should_not_run)
    assert planner.plan("msg-1").actions == []
    assert called["model"] is False


def test_missing_information_is_surfaced_not_invented(wired, monkeypatch) -> None:
    with_gap = dict(
        MODEL_REPLY,
        body="Dear Dr. Chiam,\n\nI will submit by [confirm date].\n",
        missing_info=["confirm date"],
    )
    monkeypatch.setattr(
        nebius_client, "chat_json", lambda *_a, **_k: (with_gap, ModelUsage(model="m", latency_ms=1))
    )
    action = planner.plan("msg-1").actions[0]
    assert action.missing_info == ["confirm date"]


def test_an_empty_body_is_an_error_not_a_blank_draft(wired, monkeypatch) -> None:
    broken = dict(MODEL_REPLY, body="   ")
    monkeypatch.setattr(
        nebius_client, "chat_json", lambda *_a, **_k: (broken, ModelUsage(model="m", latency_ms=1))
    )
    with pytest.raises(UpstreamError):
        planner.plan("msg-1")


def test_the_cached_analysis_is_reused(wired) -> None:
    """Planning must not pay for a second extraction on the same email."""
    planner.plan("msg-1")
    assert wired["calls"]["analyse"] == 0
