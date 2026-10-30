"""The watcher decides who gets interrupted.

Most of these tests are about restraint rather than detection. Finding new mail
is a list call; the hard part is not firing a card for a newsletter, not firing
twenty cards on first run, and not firing the same card twice. Each of those is
a way a background watcher gets uninstalled, so each gets a test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.schemas import (
    ActionItem,
    ClaimKind,
    Deadline,
    EmailAnalysis,
    EmailCategory,
    EmailSummaryItem,
)
from backend.services import store, watcher

TODAY = date(2026, 10, 30)


def summary(
    message_id: str,
    *,
    subject: str = "A subject",
    sender: str = "Ana Silva <ana@example.com>",
    labels: list[str] | None = None,
    snippet: str = "something",
    when: datetime | None = None,
) -> EmailSummaryItem:
    return EmailSummaryItem(
        id=message_id,
        subject=subject,
        sender=sender,
        labels=labels or ["INBOX", "UNREAD"],
        snippet=snippet,
        date=when or datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
    )


def analysis(
    message_id: str = "m1",
    *,
    action_required: bool = True,
    deadline: date | None = None,
    tasks: int = 1,
    category: EmailCategory = EmailCategory.ACTION_REQUIRED,
) -> EmailAnalysis:
    items = [
        ActionItem(
            title=f"Task {index}",
            why="stated in the email",
            kind=ClaimKind.EXPLICIT,
            deadline=(
                Deadline(text="soon", date=deadline, kind=ClaimKind.EXPLICIT)
                if deadline
                else None
            ),
        )
        for index in range(tasks)
    ]
    return EmailAnalysis(
        message_id=message_id,
        summary="The lecturer wants the report by the end of the week.",
        category=category,
        action_required=action_required,
        action_items=items,
        risk_summary="You lose marks.",
        model="fake-model",
    )


# --------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------


def test_sender_address_handles_display_names() -> None:
    assert watcher.sender_address("Ana Silva <Ana@Example.COM>") == "ana@example.com"
    assert watcher.sender_address("plain@example.com") == "plain@example.com"
    assert watcher.sender_address("") == ""


@pytest.mark.parametrize(
    "email",
    [
        summary("1", sender="Notion <no-reply@notion.so>"),
        summary("2", sender="Digest <noreply@substack.com>"),
        summary("3", sender="Shop <promo@shop.com>", labels=["INBOX", "CATEGORY_PROMOTIONS"]),
        summary("4", sender="Someone <a@b.com>", labels=["INBOX", "CATEGORY_SOCIAL"]),
        summary("5", sender="me@example.com"),
        summary("6", subject="", snippet=""),
    ],
)
def test_noise_is_ignored(email: EmailSummaryItem) -> None:
    assert watcher.is_noise(email, account="me@example.com") is True


def test_a_real_message_is_not_noise() -> None:
    email = summary("9", sender="Dr Lim <lim@um.edu.my>", subject="Thesis submission")
    assert watcher.is_noise(email, account="me@example.com") is False


def test_a_human_whose_name_contains_no_reply_is_not_noise() -> None:
    # The marker is matched against the address, not the display name, or a
    # person called "No Reply Needed Ltd" would never be announced.
    email = summary("10", sender="No Reply Needed Ltd <hello@nr.example>")
    assert watcher.is_noise(email) is False


# --------------------------------------------------------------------------
# Urgency
# --------------------------------------------------------------------------


def test_information_only_is_level_one() -> None:
    assert watcher.urgency(analysis(action_required=False), today=TODAY) == 1


def test_action_without_a_deadline_is_level_two() -> None:
    assert watcher.urgency(analysis(deadline=None), today=TODAY) == 2


def test_a_deadline_tomorrow_is_level_three() -> None:
    tomorrow = TODAY + timedelta(days=1)
    assert watcher.urgency(analysis(deadline=tomorrow), today=TODAY) == 3


def test_an_overdue_deadline_is_level_three() -> None:
    assert watcher.urgency(analysis(deadline=TODAY - timedelta(days=3)), today=TODAY) == 3


def test_a_deadline_next_week_is_level_two() -> None:
    assert watcher.urgency(analysis(deadline=TODAY + timedelta(days=6)), today=TODAY) == 2


# --------------------------------------------------------------------------
# The sentence on the card
# --------------------------------------------------------------------------


def test_verdict_names_the_deadline_instead_of_describing_the_email() -> None:
    line = watcher.verdict_line(analysis(deadline=TODAY), 3, today=TODAY)
    assert line == "Due today"


def test_verdict_says_overdue_when_the_date_has_passed() -> None:
    line = watcher.verdict_line(analysis(deadline=TODAY - timedelta(days=2)), 3, today=TODAY)
    assert line.startswith("Overdue")


def test_verdict_counts_tasks() -> None:
    assert watcher.verdict_line(analysis(tasks=3), 2, today=TODAY) == "Needs you · 3 tasks"


def test_verdict_for_information_is_reassuring() -> None:
    line = watcher.verdict_line(analysis(action_required=False), 1, today=TODAY)
    assert "nothing to do" in line


# --------------------------------------------------------------------------
# Cycles
# --------------------------------------------------------------------------


def _scan(feed, analysed, notified, *, now=None):
    calls: list[str] = []

    def _analyse(email):
        calls.append(email.id)
        return analysed(email), None

    result = watcher.scan_once(
        notify=notified.append,
        fetch=lambda: list(feed),
        analyse=_analyse,
        now=now or datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
    )
    return result, calls


def test_the_first_cycle_records_a_baseline_and_stays_silent(temp_db: Path) -> None:
    store.init_db()
    inbox = [summary("a"), summary("b"), summary("c")]

    result, calls = _scan(inbox, lambda e: analysis(e.id, deadline=TODAY), [])

    assert result.baselined == 3
    assert result.notified == 0
    assert calls == [], "the baseline must not spend money on inference"
    assert store.watcher_baselined() is True


def test_mail_that_arrives_after_the_baseline_is_analysed_and_announced(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    store.mark_seen(["old"])
    inbox = [summary("old"), summary("new")]
    notified: list = []

    result, calls = _scan(
        inbox, lambda e: analysis(e.id, deadline=TODAY), notified
    )

    assert calls == ["new"]
    assert result.analysed == 1
    assert result.notified == 1
    assert notified[0].verdict == "Due today"


def test_the_same_message_is_announced_only_once(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    inbox = [summary("new")]
    notified: list = []

    _scan(inbox, lambda e: analysis(e.id), notified)
    result, calls = _scan(inbox, lambda e: analysis(e.id), notified)

    assert len(notified) == 1
    assert calls == [], "the second cycle must not re-run the model"
    assert result.checked == 1


def test_quiet_mail_is_cached_but_not_announced(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    notified: list = []

    result, _ = _scan(
        [summary("info")],
        lambda e: analysis(e.id, action_required=False, tasks=0),
        notified,
    )

    assert result.analysed == 1
    assert notified == [], "information only is not worth a card"
    assert store.cache_lookup("info") is not None, "but it is pre-analysed for the UI"


def test_a_burst_is_capped_and_the_rest_waits_for_the_next_cycle(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    inbox = [
        summary(f"m{index}", when=datetime(2026, 10, 30, 9, index, tzinfo=timezone.utc))
        for index in range(5)
    ]
    notified: list = []

    first, first_calls = _scan(inbox, lambda e: analysis(e.id), notified)
    assert first_calls == ["m0", "m1", "m2"], "oldest first, and only three"
    assert first.notified == 3

    second, second_calls = _scan(inbox, lambda e: analysis(e.id), notified)
    assert second_calls == ["m3", "m4"], "the rest are picked up, not dropped"
    assert len(notified) == 5


def test_noise_is_marked_seen_so_it_is_not_reconsidered_every_minute(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    inbox = [summary("promo", labels=["INBOX", "CATEGORY_PROMOTIONS"])]
    notified: list = []

    first, calls = _scan(inbox, lambda e: analysis(e.id), notified)
    assert first.ignored == 1
    assert calls == [], "a promotion must not cost an inference call"

    second, _ = _scan(inbox, lambda e: analysis(e.id), notified)
    assert second.ignored == 0 and second.checked == 1
    assert store.seen_ids(["promo"]) == {"promo"}


def test_a_failing_analysis_does_not_stop_the_cycle(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    inbox = [summary("bad"), summary("good")]
    notified: list = []

    def _analyse(email):
        if email.id == "bad":
            raise RuntimeError("nebius said no")
        return analysis(email.id), None

    result = watcher.scan_once(
        notify=notified.append,
        fetch=lambda: list(inbox),
        analyse=_analyse,
        now=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
    )

    assert result.failed == 1
    assert result.analysed == 1
    assert [n.message_id for n in notified] == ["good"]
    assert store.seen_ids(["bad"]) == {"bad"}, "a poison message must not block the queue"


def test_a_fetch_failure_returns_instead_of_raising(temp_db: Path) -> None:
    store.init_db()

    def _boom():
        raise RuntimeError("gmail is down")

    result = watcher.scan_once(fetch=_boom, notify=lambda n: None)

    assert result.failed == 1
    assert result.checked == 0


def test_a_cached_analysis_is_reused_without_a_model_call(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    store.cache_store(analysis("known", deadline=TODAY))
    notified: list = []

    result, calls = _scan([summary("known")], lambda e: analysis(e.id), notified)

    assert calls == [], "already analysed, so nothing to pay for"
    assert result.cached == 1
    assert result.notified == 1


def test_reset_makes_the_current_inbox_replayable(temp_db: Path) -> None:
    store.init_db()
    store.mark_baselined()
    inbox = [summary("again")]
    notified: list = []

    _scan(inbox, lambda e: analysis(e.id), notified)
    assert len(notified) == 1

    store.forget_seen()
    _scan(inbox, lambda e: analysis(e.id), notified)

    assert len(notified) == 2, "the demo can be re-recorded without new mail"
    assert store.watcher_baselined() is True, "reset must not trigger a silent baseline"
