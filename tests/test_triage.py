"""The schedule-aware triage (FR-09).

Two kinds of test here, and they are deliberately separate:

- the **arithmetic** (does an event overlap a lecture) is pure and exhaustively
  testable, so it is tested directly;
- the **verdict** is a table on (clash?, score vs threshold), so every cell of
  that table gets a test.

The model is stubbed everywhere. What the model contributes is extraction, and
the point of these tests is that no model output can produce a verdict the
table would not produce.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from backend.schemas import (
    BusyBlock,
    EmailAnalysis,
    EmailCategory,
    EmailDetail,
    EventWindow,
    ModelUsage,
    ScheduleProfile,
    TriageVerdict,
    ValueAssessment,
)
from backend.services import analyzer, nebius_client, schedule, store, triage

# 2026-11-03 is a Tuesday, 2026-11-05 a Thursday.
TUE = datetime(2026, 11, 3, 10, 0)
TUE_END = datetime(2026, 11, 3, 12, 0)


def profile(**overrides) -> ScheduleProfile:
    base = dict(
        role="student",
        busy_blocks=[
            BusyBlock(weekday=1, start="09:00", end="11:00", label="Algorithms lecture"),
            BusyBlock(weekday=3, start="09:00", end="11:00", label="Lab"),
        ],
        value_threshold=4,
    )
    base.update(overrides)
    return ScheduleProfile(**base)


def make_email(**overrides) -> EmailDetail:
    base = dict(
        id="msg-1",
        thread_id="thread-1",
        subject="AI Hackathon 2026 - registrations open",
        sender="Events <events@example.org>",
        headers={"from": "Events <events@example.org>"},
        body_text="The hackathon runs 2-6 November 2026. Prizes worth USD 10,000.",
    )
    base.update(overrides)
    return EmailDetail(**base)


def make_analysis() -> EmailAnalysis:
    return EmailAnalysis(
        message_id="msg-1",
        thread_id="thread-1",
        summary="A hackathon runs 2-6 November 2026 with a USD 10,000 prize.",
        category=EmailCategory.INFORMATION_ONLY,
        action_required=False,
        model="nvidia/nemotron-3-super-120b-a12b",
    )


# --------------------------------------------------------------------------
# Overlap arithmetic
# --------------------------------------------------------------------------


def test_an_event_inside_a_lecture_is_a_conflict() -> None:
    conflicts = schedule.find_conflicts(profile(), TUE, TUE_END)
    assert len(conflicts) == 1
    assert conflicts[0].busy_label == "Algorithms lecture"
    assert conflicts[0].start == datetime(2026, 11, 3, 10, 0)
    assert conflicts[0].end == datetime(2026, 11, 3, 11, 0)


def test_an_event_after_the_lecture_is_not_a_conflict() -> None:
    assert schedule.find_conflicts(
        profile(), datetime(2026, 11, 3, 12, 0), datetime(2026, 11, 3, 14, 0)
    ) == []


def test_an_event_that_ends_exactly_when_the_lecture_starts_does_not_clash() -> None:
    """Touching intervals are not overlapping ones. Half-open, like a calendar."""
    assert schedule.find_conflicts(
        profile(), datetime(2026, 11, 3, 8, 0), datetime(2026, 11, 3, 9, 0)
    ) == []


def test_a_multi_day_event_reports_one_conflict_per_clashing_day() -> None:
    conflicts = schedule.find_conflicts(
        profile(), datetime(2026, 11, 2, 0, 0), datetime(2026, 11, 6, 23, 59)
    )
    labels = [c.busy_label for c in conflicts]
    assert labels == ["Algorithms lecture", "Lab"]
    assert [c.on_date.day for c in conflicts] == [3, 5]


def test_a_different_weekday_is_not_a_conflict() -> None:
    assert schedule.find_conflicts(
        profile(), datetime(2026, 11, 4, 10, 0), datetime(2026, 11, 4, 12, 0)
    ) == []


def test_an_empty_schedule_never_conflicts() -> None:
    assert schedule.find_conflicts(ScheduleProfile(), TUE, TUE_END) == []


def test_a_backwards_window_is_ignored_rather_than_guessed() -> None:
    assert schedule.find_conflicts(profile(), TUE_END, TUE) == []


def test_overnight_blocks_are_rejected_at_the_schema() -> None:
    with pytest.raises(ValueError):
        BusyBlock(weekday=0, start="22:00", end="02:00")


def test_the_summary_is_readable_by_a_human_and_by_the_model() -> None:
    text = schedule.summarise(profile(notes="only paid AI events"))
    assert "Tue 09:00-11:00 Algorithms lecture" in text
    assert "NOTES FROM THE USER: only paid AI events" in text


# --------------------------------------------------------------------------
# The verdict table
# --------------------------------------------------------------------------


def test_conflict_and_high_value_is_attend_anyway() -> None:
    event = EventWindow(start=TUE, end=TUE_END, precision="exact")
    conflicts = schedule.find_conflicts(profile(), TUE, TUE_END)
    verdict, why = triage.decide(profile(), event, ValueAssessment(score=5), conflicts)
    assert verdict is TriageVerdict.ATTEND_DESPITE_CONFLICT
    assert "Algorithms lecture" in why


def test_conflict_and_low_value_is_decline() -> None:
    event = EventWindow(start=TUE, end=TUE_END, precision="exact")
    conflicts = schedule.find_conflicts(profile(), TUE, TUE_END)
    verdict, why = triage.decide(profile(), event, ValueAssessment(score=2), conflicts)
    assert verdict is TriageVerdict.DECLINE_CONFLICT
    assert "2/5" in why and "4/5" in why


def test_a_free_slot_and_a_decent_event_is_attend() -> None:
    verdict, _ = triage.decide(
        profile(), EventWindow(start=TUE, end=TUE_END), ValueAssessment(score=3), []
    )
    assert verdict is TriageVerdict.ATTEND


def test_a_free_slot_and_a_weak_event_is_skip() -> None:
    verdict, _ = triage.decide(
        profile(), EventWindow(start=TUE, end=TUE_END), ValueAssessment(score=1), []
    )
    assert verdict is TriageVerdict.SKIP_NOT_WORTH_IT


def test_the_threshold_is_the_users_own_bar() -> None:
    """Raising the bar turns 'attend anyway' into 'decline' without other changes."""
    strict = profile(value_threshold=5)
    conflicts = schedule.find_conflicts(strict, TUE, TUE_END)
    verdict, _ = triage.decide(
        strict, EventWindow(start=TUE, end=TUE_END), ValueAssessment(score=4), conflicts
    )
    assert verdict is TriageVerdict.DECLINE_CONFLICT


# --------------------------------------------------------------------------
# The full path, with the model stubbed
# --------------------------------------------------------------------------


@pytest.fixture
def wired(monkeypatch, temp_db):
    """A stored schedule, a cached analysis, and a scriptable model."""
    store.save_schedule(profile())
    store.cache_store(make_analysis())
    monkeypatch.setattr(triage.gmail_client, "get_email", lambda _mid: make_email())
    monkeypatch.setattr(
        analyzer, "analyse",
        lambda *_a, **_k: (make_analysis(), ModelUsage(model="fast", latency_ms=1), None),
    )
    box = {"reply": {}, "calls": 0}

    def fake_chat(*_a, **_k):
        box["calls"] += 1
        return dict(box["reply"]), ModelUsage(model="reason", latency_ms=5)

    monkeypatch.setattr(nebius_client, "chat_json", fake_chat)
    return box


def model_reply(**overrides) -> dict:
    base = {
        "is_opportunity": True,
        "event": {
            "name": "AI Hackathon",
            "start": "2026-11-03T10:00",
            "end": "2026-11-03T12:00",
            "commitment_hours": 2,
            "location": "online",
            "precision": "exact",
        },
        "value": {"score": 5, "reasons": ["USD 10,000 prize"]},
        "missing": [],
        "notes": "A paid hackathon in your field.",
    }
    base.update(overrides)
    return base


def test_without_a_schedule_we_ask_instead_of_guessing(wired, monkeypatch) -> None:
    store.save_schedule(ScheduleProfile())  # nothing configured
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.NO_SCHEDULE
    assert "tell me your week" in result.reasoning.lower()
    # The model is not consulted: there is nothing to compare its answer to.
    assert wired["calls"] == 0


def test_a_clash_with_a_great_event_says_go_anyway(wired) -> None:
    wired["reply"] = model_reply()
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.ATTEND_DESPITE_CONFLICT
    assert [c.busy_label for c in result.conflicts] == ["Algorithms lecture"]
    assert "Algorithms lecture" in result.reasoning


def test_a_clash_with_a_weak_event_says_decline(wired) -> None:
    wired["reply"] = model_reply(value={"score": 2, "reasons": ["generic webinar"]})
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.DECLINE_CONFLICT


def test_an_email_that_is_not_an_invitation_is_labelled_as_such(wired) -> None:
    wired["reply"] = model_reply(is_opportunity=False)
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.NOT_AN_EVENT
    assert result.is_opportunity is False


def test_an_invitation_without_a_date_asks_for_the_date(wired) -> None:
    wired["reply"] = model_reply(
        event={"name": "Career fair", "start": None, "end": None, "precision": "unknown"}
    )
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.UNKNOWN_TIMING
    assert result.event is not None and result.event.start is None


def test_a_date_only_invitation_blocks_the_whole_day_and_says_so(wired) -> None:
    """No hours in the email means the whole day is assumed busy, and we say so.

    The clash exists only because of that assumption: a 14:00-16:00 fair would
    have missed the 09:00-11:00 lecture entirely.
    """
    wired["reply"] = model_reply(
        event={
            "name": "Career fair",
            "start": "2026-11-03T00:00",
            "end": "2026-11-03T23:59",
            "precision": "date_only",
        }
    )
    result = triage.assess("msg-1")
    assert len(result.conflicts) == 1
    assert result.verdict is TriageVerdict.ATTEND_DESPITE_CONFLICT  # score 5 >= 4
    assert "treated the whole day as busy" in result.reasoning


def test_an_absurd_score_is_clamped_to_the_scale(wired) -> None:
    """A model (or a hostile email) cannot buy a verdict with score=99."""
    wired["reply"] = model_reply(value={"score": 99, "reasons": []})
    result = triage.assess("msg-1")
    assert result.value is not None and result.value.score == 5


def test_a_model_supplied_verdict_field_is_ignored(wired) -> None:
    """The verdict is computed here, so a 'verdict' key in the JSON does nothing."""
    wired["reply"] = model_reply(
        verdict="attend_despite_conflict",
        value={"score": 1, "reasons": ["nothing special"]},
    )
    result = triage.assess("msg-1")
    assert result.verdict is TriageVerdict.DECLINE_CONFLICT
