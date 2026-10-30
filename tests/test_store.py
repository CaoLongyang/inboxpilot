"""Persistence tests (task A9).

Two behaviours matter more than the rest and are tested hardest:

- a stored analysis round-trips **losslessly**, because the UI shows dates,
  claim kinds and evidence quotes and any of them silently degrading would be a
  correctness bug rather than a cosmetic one;
- the idempotency key is enforced by the database, because the whole approval
  gate rests on "the same approval cannot create two drafts".
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from backend.errors import DuplicateRequest
from backend.schemas import (
    ActionItem,
    ClaimKind,
    Deadline,
    EmailAnalysis,
    EmailCategory,
    Evidence,
    ExecutionResult,
    ExecutionStatus,
    ModelUsage,
    ProposedAction,
    ActionKind,
)
from backend.services import store


def build_analysis(message_id: str = "msg-1") -> EmailAnalysis:
    return EmailAnalysis(
        message_id=message_id,
        thread_id="thread-1",
        summary="A lecturer asks for the assignment slides before Friday.",
        category=EmailCategory.ACTION_REQUIRED,
        action_required=True,
        key_facts=[
            {
                "text": "The deadline is 24 October 2026.",
                "kind": ClaimKind.EXPLICIT,
                "evidence": [
                    {
                        "message_id": message_id,
                        "field": "body",
                        "quote": "Please submit the slides by 24 October 2026.",
                    }
                ],
            }
        ],
        action_items=[
            {
                "title": "Send the assignment slides",
                "why": "The email asks for them before the deadline.",
                "kind": ClaimKind.EXPLICIT,
                "deadline": {
                    "text": "24 October 2026",
                    "date": date(2026, 10, 24),
                    "kind": ClaimKind.EXPLICIT,
                    "evidence": [
                        {
                            "message_id": message_id,
                            "field": "body",
                            "quote": "Please submit the slides by 24 October 2026.",
                        }
                    ],
                },
                "risk": "The assignment would be counted as late.",
                "needs_user_input": ["the slides file itself"],
            }
        ],
        uncertainties=["The email does not say which format is expected."],
        model="nvidia/llama-3.1-nemotron-70b-instruct",
    )


def build_usage() -> ModelUsage:
    return ModelUsage(
        model="nvidia/llama-3.1-nemotron-70b-instruct",
        latency_ms=1234,
        prompt_tokens=800,
        completion_tokens=220,
        total_tokens=1020,
    )


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


def test_init_creates_the_three_tables(temp_db: Path) -> None:
    store.init_db()
    with sqlite3.connect(temp_db) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"analysis_results", "action_proposals", "execution_logs"} <= names


def test_init_is_idempotent_and_keeps_data(temp_db: Path) -> None:
    store.init_db()
    store.cache_store(build_analysis(), build_usage())
    store.init_db()  # must not drop or duplicate anything
    assert store.stats()["analysis_results"] == 1


def test_init_creates_the_parent_directory(tmp_path: Path, monkeypatch) -> None:
    from backend import config

    nested = tmp_path / "a" / "b" / "c" / "store.db"
    monkeypatch.setattr(config.get_settings(), "database_path", str(nested))
    store.init_db()
    assert nested.exists()


def test_try_init_db_reports_failure_instead_of_raising(
    tmp_path: Path, monkeypatch
) -> None:
    from backend import config

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    # The parent of the database is a regular file, so mkdir must fail.
    monkeypatch.setattr(
        config.get_settings(), "database_path", str(blocker / "store.db")
    )
    assert store.try_init_db() is False


# --------------------------------------------------------------------------
# analysis_results
# --------------------------------------------------------------------------


def test_analysis_round_trip_is_lossless(temp_db: Path) -> None:
    store.init_db()
    original = build_analysis()
    assert store.cache_store(original, build_usage())

    hit = store.cache_lookup(original.message_id)
    assert hit is not None
    restored, usage = hit

    assert restored == original
    # Dates and enums specifically, not just "it parsed".
    assert restored.action_items[0].deadline is not None
    assert restored.action_items[0].deadline.date == date(2026, 10, 24)
    assert restored.action_items[0].deadline.kind is ClaimKind.EXPLICIT
    assert restored.key_facts[0].evidence[0].quote.startswith("Please submit")
    assert restored.category is EmailCategory.ACTION_REQUIRED

    assert usage is not None
    assert usage.latency_ms == 1234
    assert usage.total_tokens == 1020


def test_cache_lookup_misses_cleanly(temp_db: Path) -> None:
    store.init_db()
    assert store.cache_lookup("never-analysed") is None


def test_second_store_updates_the_same_row(temp_db: Path) -> None:
    store.init_db()
    first = build_analysis()
    store.cache_store(first, build_usage())

    second = first.model_copy(update={"summary": "Rewritten summary."})
    store.cache_store(second, build_usage())

    assert store.stats()["analysis_results"] == 1
    hit = store.cache_lookup(first.message_id)
    assert hit is not None and hit[0].summary == "Rewritten summary."
    with sqlite3.connect(temp_db) as conn:
        refreshed = conn.execute(
            "SELECT refreshed_count FROM analysis_results WHERE message_id = ?",
            (first.message_id,),
        ).fetchone()[0]
    assert refreshed == 1


def test_cache_without_usage_still_round_trips(temp_db: Path) -> None:
    store.init_db()
    analysis = build_analysis()
    assert store.cache_store(analysis, None)
    hit = store.cache_lookup(analysis.message_id)
    assert hit is not None
    assert hit[1] is None  # no usage recorded, and none invented
    assert hit[0].model == analysis.model


def test_corrupt_payload_is_a_miss_not_a_crash(temp_db: Path) -> None:
    store.init_db()
    store.cache_store(build_analysis(), build_usage())
    with sqlite3.connect(temp_db) as conn:
        conn.execute(
            "UPDATE analysis_results SET payload = ? WHERE message_id = ?",
            ("{not json", "msg-1"),
        )
    # The row is unreadable, so the caller re-analyses instead of returning 500.
    assert store.cache_lookup("msg-1") is None


def test_corrupt_database_file_is_a_miss_not_a_crash(
    tmp_path: Path, monkeypatch
) -> None:
    from backend import config

    broken = tmp_path / "broken.db"
    broken.write_text("this is not a sqlite database at all", encoding="utf-8")
    monkeypatch.setattr(config.get_settings(), "database_path", str(broken))

    assert store.cache_lookup("msg-1") is None
    assert store.cache_store(build_analysis(), build_usage()) is False


def test_cache_delete(temp_db: Path) -> None:
    store.init_db()
    store.cache_store(build_analysis(), build_usage())
    assert store.cache_delete("msg-1") is True
    assert store.cache_lookup("msg-1") is None
    assert store.cache_delete("msg-1") is False


def test_account_is_recorded(temp_db: Path) -> None:
    store.init_db()
    store.cache_store(build_analysis(), build_usage(), account="me@example.com")
    with sqlite3.connect(temp_db) as conn:
        account = conn.execute(
            "SELECT account FROM analysis_results WHERE message_id = ?", ("msg-1",)
        ).fetchone()[0]
    assert account == "me@example.com"


# --------------------------------------------------------------------------
# action_proposals
# --------------------------------------------------------------------------


def build_proposal(proposal_id: str = "prop-1") -> ProposedAction:
    return ProposedAction(
        id=proposal_id,
        message_id="msg-1",
        kind=ActionKind.CREATE_DRAFT,
        title="Reply confirming the deadline",
        why="The lecturer asked for confirmation.",
        requires_scopes=["https://www.googleapis.com/auth/gmail.compose"],
        missing_info=["the slides file"],
        draft={
            "to": ["lecturer@example.com"],
            "subject": "Re: Assignment slides",
            "body": "I will send them before 24 October.",
            "in_reply_to": "msg-1",
            "thread_id": "thread-1",
        },
    )


def test_proposal_round_trip(temp_db: Path) -> None:
    store.init_db()
    original = build_proposal()
    store.save_proposal(original)

    restored = store.get_proposal("prop-1")
    assert restored == original
    assert restored is not None
    assert restored.draft is not None
    assert restored.draft.to == ["lecturer@example.com"]


def test_unknown_proposal_is_none(temp_db: Path) -> None:
    store.init_db()
    assert store.get_proposal("prop-does-not-exist") is None


def test_unreadable_proposal_fails_closed(temp_db: Path) -> None:
    store.init_db()
    store.save_proposal(build_proposal())
    with sqlite3.connect(temp_db) as conn:
        conn.execute(
            "UPDATE action_proposals SET payload = ? WHERE id = ?", ("[]", "prop-1")
        )
    # Not an exception: the approval gate treats "cannot reconstruct" as "no".
    assert store.get_proposal("prop-1") is None


def test_proposal_status_transitions(temp_db: Path) -> None:
    store.init_db()
    store.save_proposal(build_proposal())
    store.set_proposal_status("prop-1", "approved")
    with sqlite3.connect(temp_db) as conn:
        status = conn.execute(
            "SELECT status FROM action_proposals WHERE id = ?", ("prop-1",)
        ).fetchone()[0]
    assert status == "approved"


# --------------------------------------------------------------------------
# execution_logs
# --------------------------------------------------------------------------


def build_result(action_id: str = "act-1") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        status=ExecutionStatus.CREATED,
        draft_id="draft-123",
        verified=True,
        message="Draft read back from Gmail.",
    )


def test_execution_round_trip(temp_db: Path) -> None:
    store.init_db()
    original = build_result()
    store.record_execution(
        original,
        idempotency_key="key-abcdefgh",
        proposal_id="prop-1",
        message_id="msg-1",
    )

    restored = store.get_execution("act-1")
    assert restored is not None
    assert restored.draft_id == "draft-123"
    assert restored.verified is True
    assert restored.status is ExecutionStatus.CREATED
    assert restored.created_at == original.created_at


def test_duplicate_idempotency_key_is_rejected(temp_db: Path) -> None:
    store.init_db()
    store.record_execution(build_result(), idempotency_key="key-abcdefgh")

    with pytest.raises(DuplicateRequest) as excinfo:
        store.record_execution(
            build_result("act-2"), idempotency_key="key-abcdefgh"
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "duplicate_request"
    # Only one row: the second attempt never reached Gmail.
    assert store.stats()["execution_logs"] == 1


def test_find_execution_by_key(temp_db: Path) -> None:
    store.init_db()
    store.record_execution(build_result(), idempotency_key="key-abcdefgh")
    found = store.find_execution_by_key("key-abcdefgh")
    assert found is not None and found.draft_id == "draft-123"
    assert store.find_execution_by_key("never-used") is None


def test_unknown_execution_is_none(temp_db: Path) -> None:
    store.init_db()
    assert store.get_execution("act-missing") is None


def test_stats_reports_counts(temp_db: Path) -> None:
    store.init_db()
    assert store.stats() == {
        "analysis_results": 0,
        "action_proposals": 0,
        "execution_logs": 0,
        "schedule_profile": 0,
        "watcher_seen": 0,
        "weekly_digest": 0,
    }
    store.cache_store(build_analysis(), build_usage())
    store.save_proposal(build_proposal())
    store.record_execution(build_result(), idempotency_key="key-abcdefgh")
    assert store.stats() == {
        "analysis_results": 1,
        "action_proposals": 1,
        "execution_logs": 1,
        "schedule_profile": 0,
        "watcher_seen": 0,
        "weekly_digest": 0,
    }
