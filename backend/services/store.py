"""SQLite persistence (task A9).

Why a database for a hackathon demo? Three concrete reasons, one per table:

1. **`analysis_results`** — analysing the same email twice costs money and
   seconds for an identical answer. The cache makes the second click instant and
   keeps the demo inside the free credit budget. It also gives us something to
   show when the model provider is slow: the last good analysis.
2. **`action_proposals`** — the approval gate (FR-07) must not live in the
   browser. A `proposal_id` the server cannot resolve is not an approval, it is
   a guess. This table is the authority on what the user was actually shown.
3. **`execution_logs`** — the idempotency key (FR-08) has to be enforced
   somewhere that cannot race. It is a `UNIQUE` constraint here, not an `if` in
   Python, because two threads can both pass an `if` before either writes.

Design notes
------------
- Standard-library `sqlite3`. No ORM: four tables, short queries, and an ORM
  would add a dependency and a layer without removing either.
- One connection per operation. FastAPI runs sync handlers in a thread pool, so
  a shared connection would need a lock around every statement.
- Payloads are stored as JSON next to the handful of columns we actually query
  or sort by. Pydantic stays the single source of truth for the shape; this
  module is durable storage, not a second definition of the contract.
- **Cache reads and writes never raise.** `/analyze` must still work on a
  read-only disk. Approval and execution records do raise, because there
  "best effort" would mean silently skipping a safety check.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from backend.config import get_settings
from backend.errors import DuplicateRequest, StorageError
from backend.schemas import (
    EmailAnalysis,
    ExecutionResult,
    ExecutionStatus,
    ModelUsage,
    ProposedAction,
    ScheduleProfile,
    WeeklyDigest,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# `payload` holds the full API model as JSON. The other columns exist only so we
# can sort, filter, or display without deserialising every row.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_results (
    message_id        TEXT PRIMARY KEY,
    thread_id         TEXT,
    account           TEXT NOT NULL DEFAULT '',
    payload           TEXT NOT NULL,
    model             TEXT NOT NULL DEFAULT '',
    -- NULL means "no usage was recorded", which is different from 0 ms.
    latency_ms        INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    analyzed_at       TEXT NOT NULL,
    refreshed_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_analysis_analyzed_at
    ON analysis_results (analyzed_at DESC);

CREATE TABLE IF NOT EXISTS action_proposals (
    id         TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposals_message
    ON action_proposals (message_id, created_at DESC);

CREATE TABLE IF NOT EXISTS execution_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id       TEXT NOT NULL,
    proposal_id     TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL,
    draft_id        TEXT,
    verified        INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_executions_action
    ON execution_logs (action_id);

-- One row, id = 'default'. A single-user demo does not need accounts, and the
-- schedule is the only state the user owns rather than the agent.
CREATE TABLE IF NOT EXISTS schedule_profile (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- What the watcher has already considered, so it does not announce the same
-- mail twice. This is a record of decisions (announced, filtered, or already
-- analysed), not a copy of the mailbox: a message id is all it holds.
CREATE TABLE IF NOT EXISTS watcher_seen (
    message_id TEXT PRIMARY KEY,
    seen_at    TEXT NOT NULL
);

-- One row, id = 'default'. Distinguishes "the watcher has never run" from
-- "the watcher ran and the inbox happened to be empty". Without it, starting
-- the watcher on an inbox with 20 unread messages would fire 20 cards, and an
-- empty inbox would make the next arrival look like a baseline.
CREATE TABLE IF NOT EXISTS watcher_state (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- One row per digest window, so re-opening the page does not re-run the model.
-- The key is "last N days", which means the digest is stale the moment a new
-- email arrives; that is accepted, and "Regenerate" is one click.
CREATE TABLE IF NOT EXISTS weekly_digest (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


# --------------------------------------------------------------------------
# Connection handling
# --------------------------------------------------------------------------


def db_path() -> Path:
    """Resolved at call time so tests can point `DATABASE_PATH` anywhere."""
    return get_settings().db_path


def _key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:  # pragma: no cover - unreadable parent directory
        return str(path)


def _create_schema(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    try:
        # WAL survives in the file itself; set once, not per connection.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    finally:
        conn.close()


def init_db(path: Path | None = None) -> Path:
    """Create the tables if they do not exist. Safe to call repeatedly."""
    target = Path(path) if path is not None else db_path()
    _create_schema(target)
    with _init_lock:
        _initialised.add(_key(target))
    log.info("store.ready path=%s schema_version=%d", target, SCHEMA_VERSION)
    return target


def try_init_db() -> bool:
    """Best-effort init for application startup. Never raises.

    Called from `create_app` so a broken database path shows up in the startup
    log instead of on the user's first analysis. A failure is not fatal: the
    cache degrades to "always miss" and every other endpoint keeps working.
    """
    try:
        init_db()
        return True
    except (sqlite3.Error, OSError) as exc:
        log.error("store.init_failed path=%s error=%s", db_path(), type(exc).__name__)
        return False


def _ensure_ready(target: Path) -> None:
    if _key(target) in _initialised:
        return
    with _init_lock:
        if _key(target) in _initialised:  # another thread won the race
            return
        # Still holding the lock: schema creation happens exactly once per path.
        _create_schema(target)
        _initialised.add(_key(target))


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    target = db_path()
    _ensure_ready(target)
    conn = sqlite3.connect(target, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dumps(model) -> str:
    # `mode="json"` turns dates and enums into JSON-safe primitives, so loading
    # the row back through `model_validate` is lossless.
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


# --------------------------------------------------------------------------
# analysis_results  (email id -> analysis, task A9's last checkbox)
# --------------------------------------------------------------------------


def cache_store(
    analysis: EmailAnalysis,
    usage: ModelUsage | None = None,
    *,
    account: str = "",
) -> bool:
    """Persist an analysis. Never raises; returns whether it was written.

    A failure here is logged and swallowed on purpose: the user already waited
    for the model, and losing the cache entry must not lose the answer.
    """
    analyzed_at = analysis.analyzed_at
    if analyzed_at.tzinfo is None:  # defensive: schema defaults are tz-aware
        analyzed_at = analyzed_at.replace(tzinfo=timezone.utc)

    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_results (
                    message_id, thread_id, account, payload, model, latency_ms,
                    prompt_tokens, completion_tokens, total_tokens, analyzed_at,
                    refreshed_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(message_id) DO UPDATE SET
                    thread_id         = excluded.thread_id,
                    account           = excluded.account,
                    payload           = excluded.payload,
                    model             = excluded.model,
                    latency_ms        = excluded.latency_ms,
                    prompt_tokens     = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens,
                    total_tokens      = excluded.total_tokens,
                    analyzed_at       = excluded.analyzed_at,
                    refreshed_count   = analysis_results.refreshed_count + 1
                """,
                (
                    analysis.message_id,
                    analysis.thread_id,
                    account,
                    _dumps(analysis),
                    usage.model if usage else analysis.model,
                    usage.latency_ms if usage else None,
                    usage.prompt_tokens if usage else None,
                    usage.completion_tokens if usage else None,
                    usage.total_tokens if usage else None,
                    analyzed_at.astimezone(timezone.utc).isoformat(),
                ),
            )
        return True
    except (sqlite3.Error, OSError) as exc:
        log.warning("store.cache_write_failed error=%s", type(exc).__name__)
        return False


def cache_lookup(message_id: str) -> tuple[EmailAnalysis, ModelUsage | None] | None:
    """Return a previously stored analysis, or None. Never raises.

    A row that no longer validates (schema changed during development) is
    treated as a miss and left in place: the caller will overwrite it on the
    next successful analysis, and deleting user data on a read is not this
    function's job.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload, model, latency_ms, prompt_tokens,"
                " completion_tokens, total_tokens"
                " FROM analysis_results WHERE message_id = ?",
                (message_id,),
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        log.warning("store.cache_read_failed error=%s", type(exc).__name__)
        return None

    if row is None:
        return None

    try:
        analysis = EmailAnalysis.model_validate(json.loads(row["payload"]))
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        log.warning(
            "store.cache_row_unusable message_id=%s error=%s",
            message_id,
            type(exc).__name__,
        )
        return None

    usage: ModelUsage | None = None
    # A NULL latency means the original call reported no usage at all. Do not
    # invent `latency_ms=0`: the UI would show "0 ms" as if it were measured.
    if row["model"] and row["latency_ms"] is not None:
        usage = ModelUsage(
            model=row["model"],
            latency_ms=row["latency_ms"],
            prompt_tokens=row["prompt_tokens"],
            completion_tokens=row["completion_tokens"],
            total_tokens=row["total_tokens"],
        )
    return analysis, usage


def cache_delete(message_id: str) -> bool:
    """Drop one cached analysis. Returns whether a row existed."""
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM analysis_results WHERE message_id = ?", (message_id,)
            )
            return cursor.rowcount > 0
    except (sqlite3.Error, OSError) as exc:
        log.warning("store.cache_delete_failed error=%s", type(exc).__name__)
        return False


# --------------------------------------------------------------------------
# action_proposals  (what the user was actually shown, FR-07)
# --------------------------------------------------------------------------


def save_proposal(proposal: ProposedAction) -> None:
    """Insert or refresh a proposal. Raises on failure: an approval must be
    durable before the UI can offer the button."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO action_proposals (id, message_id, kind, payload, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(id) DO UPDATE SET
                payload = excluded.payload,
                kind    = excluded.kind
            """,
            (
                proposal.id,
                proposal.message_id,
                proposal.kind.value,
                _dumps(proposal),
                _utcnow(),
            ),
        )


def get_proposal(proposal_id: str) -> ProposedAction | None:
    """Load a proposal by id. Returns None if it is missing or unreadable.

    Fails closed: an approval the server cannot reconstruct is treated as no
    approval at all.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM action_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        return ProposedAction.model_validate(json.loads(row["payload"]))
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        log.error(
            "store.proposal_unusable proposal_id=%s error=%s",
            proposal_id,
            type(exc).__name__,
        )
        return None


def set_proposal_status(proposal_id: str, status: str) -> None:
    """pending -> approved / rejected. Used by the Day 4 approval gate."""
    with _connect() as conn:
        conn.execute(
            "UPDATE action_proposals SET status = ? WHERE id = ?",
            (status, proposal_id),
        )


# --------------------------------------------------------------------------
# execution_logs  (idempotency, FR-08 / FR-09)
# --------------------------------------------------------------------------


def record_execution(
    result: ExecutionResult,
    *,
    idempotency_key: str,
    proposal_id: str = "",
    message_id: str = "",
) -> None:
    """Append an execution outcome.

    The `UNIQUE` constraint on `idempotency_key` is the real guarantee: two
    concurrent retries of the same approval produce one row and one draft, no
    matter how the threads interleave.

    Raises `DuplicateRequest` when the key was already used. Day 4 decides
    whether to replay the stored result instead of returning 409; either way
    the second call must not reach Gmail.
    """
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_logs (
                    action_id, proposal_id, message_id, idempotency_key,
                    status, draft_id, verified, message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.action_id,
                    proposal_id,
                    message_id,
                    idempotency_key,
                    result.status.value,
                    result.draft_id,
                    1 if result.verified else 0,
                    result.message,
                    result.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )
    except sqlite3.IntegrityError as exc:
        log.info("store.duplicate_execution key=%s", idempotency_key)
        raise DuplicateRequest(
            "This approval was already executed",
            details={"idempotency_key": idempotency_key},
        ) from exc


def _row_to_execution(row: sqlite3.Row) -> ExecutionResult:
    return ExecutionResult(
        action_id=row["action_id"],
        status=ExecutionStatus(row["status"]),
        draft_id=row["draft_id"],
        verified=bool(row["verified"]),
        message=row["message"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def update_execution(result: ExecutionResult, *, idempotency_key: str) -> None:
    """Turn a claimed `pending` row into its final outcome.

    The row is inserted *before* Gmail is called and updated afterwards, so the
    UNIQUE key is held for the whole duration of the write. A crash between the
    two leaves the row `pending`, which the approval gate treats as "outcome
    unknown, do not retry" — the only safe reading when a request may have
    reached Gmail without its response coming back.
    """
    with _connect() as conn:
        conn.execute(
            """
            UPDATE execution_logs
               SET status = ?, draft_id = ?, verified = ?, message = ?
             WHERE idempotency_key = ?
            """,
            (
                result.status.value,
                result.draft_id,
                1 if result.verified else 0,
                result.message,
                idempotency_key,
            ),
        )


def get_execution(action_id: str) -> ExecutionResult | None:
    """Latest outcome for an action id, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM execution_logs WHERE action_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (action_id,),
        ).fetchone()
    return _row_to_execution(row) if row is not None else None


def find_execution_by_key(idempotency_key: str) -> ExecutionResult | None:
    """Look up the outcome a previous identical approval produced."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM execution_logs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    return _row_to_execution(row) if row is not None else None


# --------------------------------------------------------------------------
# The user's weekly schedule (FR-09)
# --------------------------------------------------------------------------

SCHEDULE_ID = "default"


def load_schedule() -> ScheduleProfile:
    """Return the saved schedule, or an empty one.

    A missing row is normal (first run) and a broken database is not the user's
    problem here: both produce an unconfigured profile, and the UI then asks
    for the timetable instead of guessing. Nothing is written back.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload FROM schedule_profile WHERE id = ?", (SCHEDULE_ID,)
            ).fetchone()
    except sqlite3.Error as exc:
        log.warning("store.schedule_read_failed type=%s", type(exc).__name__)
        return ScheduleProfile()
    if row is None:
        return ScheduleProfile()
    try:
        profile = ScheduleProfile.model_validate_json(row["payload"])
    except ValidationError as exc:
        log.warning("store.schedule_corrupt type=%s", type(exc).__name__)
        return ScheduleProfile()
    return profile.model_copy(update={"configured": _is_configured(profile)})


def save_schedule(profile: ScheduleProfile) -> ScheduleProfile:
    """Persist the schedule. Raises when it cannot be saved.

    Unlike the analysis cache, a silent failure here would tell the user their
    timetable is in effect when it is not, and every later verdict would be
    computed against nothing.
    """
    payload = profile.model_copy(update={"configured": _is_configured(profile)})
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO schedule_profile (id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (SCHEDULE_ID, _dumps(payload), _utcnow()),
            )
    except sqlite3.Error as exc:
        raise StorageError("The schedule could not be saved") from exc
    log.info(
        "store.schedule_saved blocks=%d notes=%d threshold=%d",
        len(payload.busy_blocks), len(payload.notes), payload.value_threshold,
    )
    return payload


def _is_configured(profile: ScheduleProfile) -> bool:
    """Enough information to say something useful about a clash.

    Notes alone are not enough: "only paid hackathons" does not tell us when
    the user is in class, so a conflict question would still be unanswerable.
    """
    return bool(profile.busy_blocks)


# --------------------------------------------------------------------------
# Watcher bookkeeping (background mail scan)
# --------------------------------------------------------------------------


WATCHER_ID = "default"


def watcher_baselined() -> bool:
    """Whether the watcher has taken its initial snapshot of the inbox.

    Never raises: if this cannot be read, reporting False means the watcher
    re-baselines, which costs one silent cycle and no notifications. The
    opposite default would risk a burst of cards for old mail.
    """
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload FROM watcher_state WHERE id = ?", (WATCHER_ID,)
            ).fetchone()
    except sqlite3.Error as exc:
        log.warning("store.watcher_state_read_failed type=%s", type(exc).__name__)
        return False
    if row is None:
        return False
    try:
        return bool(json.loads(row["payload"]).get("baselined"))
    except (json.JSONDecodeError, AttributeError):
        return False


def mark_baselined() -> None:
    """Record that the first snapshot has been taken. Best effort."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO watcher_state (id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (WATCHER_ID, json.dumps({"baselined": True}), _utcnow()),
            )
    except sqlite3.Error as exc:
        log.warning("store.watcher_state_write_failed type=%s", type(exc).__name__)


def seen_ids(message_ids: list[str]) -> set[str]:
    """Which of these ids the watcher has already dealt with. Never raises.

    On failure it returns an empty set, so every message looks new. That is the
    safe direction: a duplicate card is a small annoyance, while silently
    dropping new mail would make the feature look broken.
    """
    if not message_ids:
        return set()
    try:
        with _connect() as conn:
            placeholders = ",".join("?" * len(message_ids))
            rows = conn.execute(
                f"SELECT message_id FROM watcher_seen WHERE message_id IN ({placeholders})",
                message_ids,
            ).fetchall()
    except sqlite3.Error as exc:
        log.warning("store.seen_read_failed type=%s", type(exc).__name__)
        return set()
    return {row["message_id"] for row in rows}


def mark_seen(message_ids: list[str]) -> None:
    """Remember these ids. Best effort: a lost row only means a repeat card."""
    if not message_ids:
        return
    now = _utcnow()
    try:
        with _connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO watcher_seen (message_id, seen_at) VALUES (?, ?)",
                [(message_id, now) for message_id in message_ids],
            )
    except sqlite3.Error as exc:
        log.warning("store.seen_write_failed type=%s", type(exc).__name__)


def forget_seen() -> int:
    """Clear which messages have been announced. Returns rows removed.

    The baseline flag is deliberately kept. Clearing it too would make the next
    cycle re-baseline and stay silent, which is the opposite of what this is
    for: replaying the current inbox — for a second take of the demo video,
    say — without waiting for genuinely new mail.
    """
    try:
        with _connect() as conn:
            removed = conn.execute("DELETE FROM watcher_seen").rowcount
    except sqlite3.Error as exc:
        raise StorageError("The watcher history could not be cleared") from exc
    log.info("store.watcher_reset removed=%d", removed)
    return removed


# --------------------------------------------------------------------------
# Weekly digest
# --------------------------------------------------------------------------


def digest_key(days: int) -> str:
    """One digest per window size. Not per calendar week, on purpose.

    A calendar week would go stale at midnight on Monday and silently show last
    week's answer. "The last 7 days" is what the button says, and it is what
    gets cached.
    """
    return f"last-{days}-days"


def load_digest(days: int) -> WeeklyDigest | None:
    """Return a stored digest, or None. Never raises."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload FROM weekly_digest WHERE id = ?", (digest_key(days),)
            ).fetchone()
    except sqlite3.Error as exc:
        log.warning("store.digest_read_failed type=%s", type(exc).__name__)
        return None
    if row is None:
        return None
    try:
        return WeeklyDigest.model_validate_json(row["payload"])
    except ValidationError as exc:
        log.warning("store.digest_corrupt type=%s", type(exc).__name__)
        return None


def save_digest(digest: WeeklyDigest) -> None:
    """Persist a digest. Best effort: failing to cache is not failing to answer."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO weekly_digest (id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
                "updated_at = excluded.updated_at",
                (digest_key(digest.days), _dumps(digest), _utcnow()),
            )
    except sqlite3.Error as exc:
        log.warning("store.digest_write_failed type=%s", type(exc).__name__)


# --------------------------------------------------------------------------
# Introspection (used by scripts/check_store.py and debugging)
# --------------------------------------------------------------------------


def stats() -> dict[str, int]:
    """Row counts per table. No email content, safe to print or log."""
    with _connect() as conn:
        return {
            table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in (
                "analysis_results", "action_proposals", "execution_logs",
                "schedule_profile", "watcher_seen", "weekly_digest",
            )
        }


__all__ = [
    "SCHEMA_VERSION",
    "cache_delete",
    "cache_lookup",
    "cache_store",
    "db_path",
    "digest_key",
    "find_execution_by_key",
    "forget_seen",
    "get_execution",
    "get_proposal",
    "init_db",
    "load_digest",
    "load_schedule",
    "mark_baselined",
    "mark_seen",
    "record_execution",
    "save_digest",
    "save_proposal",
    "save_schedule",
    "seen_ids",
    "set_proposal_status",
    "stats",
    "try_init_db",
    "update_execution",
    "watcher_baselined",
]
