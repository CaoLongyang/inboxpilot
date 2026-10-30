"""Watch the inbox, analyse what arrives, and say something before being asked.

The product shift this represents: InboxPilot stops being a page you visit and
becomes something that already read your mail. Two consequences drive the
design.

**The model extracts; Python decides.** The watcher never asks a model "is this
urgent?". It asks the analyser for facts (is action required, is there a
deadline) and then applies a rule. That keeps the notification policy readable,
testable, and free of model drift — the same rule always gives the same card.

**Silence is the default.** A watcher that announces every message is a watcher
you turn off. Cards are for mail that needs the user today or tomorrow;
everything else is analysed, cached, and left alone. The urgency threshold is
the single most important knob in this module.

Why polling rather than push: Gmail's push notifications need a public HTTPS
endpoint, which this project deliberately does not have. IMAP IDLE would be
instant without one, but it requires the full-mailbox `https://mail.google.com/`
scope — an enormous privacy downgrade for a few seconds of latency. Polling the
list endpoint costs about 0.6% of the daily Gmail quota at one request a minute.

Deduplication is by message id in SQLite, not by unread state. "Unread" is a
label the user can change on any device: reading a message on a phone would
make it invisible here, and marking an old one unread would make it look new.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from backend.config import get_settings
from backend.schemas import EmailAnalysis, EmailSummaryItem, ModelUsage
from backend.services import analyzer, gmail_client, store, token_store
from backend.services.notify import Notification

log = logging.getLogger(__name__)

# `in:inbox` rather than `is:unread`: a message read on a phone is still mail
# that arrived, and the seen-table already prevents repeats.
POLL_QUERY = "in:inbox newer_than:2d"
MAX_RESULTS = 10

# A burst of mail (a mailing list, a busy thread) must not become a burst of
# cards, and must not become a burst of paid inference calls. Anything beyond
# this is picked up on the next cycle, because unprocessed messages are simply
# left out of the seen table.
MAX_PER_CYCLE = 3

# 3 = act today, 2 = worth a look. Level 1 is analysed and cached but silent.
NOTIFY_FROM = 2

DEFAULT_INTERVAL = 60.0

_SENDER_RE = re.compile(r"<([^>]+)>")
# Local parts that never want a human reply. Matched against the address only,
# never the display name, so "No Reply Needed Ltd" as a person still gets in.
_NOISE_LOCAL = (
    "no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "notifications", "notification",
)
_NOISE_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}


# --------------------------------------------------------------------------
# Decisions (pure, no I/O)
# --------------------------------------------------------------------------


def sender_address(sender: str) -> str:
    """`"Ana <ana@x.com>"` -> `"ana@x.com"`. Falls back to the raw string."""
    match = _SENDER_RE.search(sender or "")
    address = match.group(1) if match else (sender or "")
    return address.strip().lower()


def is_noise(email: EmailSummaryItem, *, account: str = "") -> bool:
    """Mail that should never produce a card.

    Promotions and social notifications are the two categories that reliably
    drown a watcher. Bulk senders are caught by their local part. Mail the user
    sent to themselves is caught by address so the demo does not announce its
    own test messages back at the presenter.
    """
    if not (email.subject or "").strip() and not (email.snippet or "").strip():
        return True
    if _NOISE_LABELS & set(email.labels):
        return True
    address = sender_address(email.sender)
    local = address.split("@", 1)[0]
    if any(marker in local for marker in _NOISE_LOCAL):
        return True
    if account and address == account.strip().lower():
        return True
    return False


def urgency(analysis: EmailAnalysis, *, today: date | None = None) -> int:
    """How much of the user's attention this deserves, 1 to 3.

    Deliberately computed from structured fields rather than asked of a model:
    a notification policy that changes between runs is not a policy.
    """
    if not analysis.action_required:
        return 1
    today = today or datetime.now(timezone.utc).date()
    deadlines = [
        item.deadline.date
        for item in analysis.action_items
        if item.deadline is not None and item.deadline.date is not None
    ]
    if deadlines and min(deadlines) <= today + timedelta(days=1):
        return 3
    return 2


def verdict_line(analysis: EmailAnalysis, level: int, *, today: date | None = None) -> str:
    """The one sentence the card exists to deliver.

    Not a summary. A card answers "do I have to deal with this now?", and a
    summary makes the reader work that out for themselves.
    """
    today = today or datetime.now(timezone.utc).date()
    deadlines = [
        item.deadline.date
        for item in analysis.action_items
        if item.deadline is not None and item.deadline.date is not None
    ]
    if level >= 3 and deadlines:
        soonest = min(deadlines)
        if soonest < today:
            return f"Overdue since {soonest:%b} {soonest.day}"
        if soonest == today:
            return "Due today"
        return f"Due tomorrow ({soonest:%b} {soonest.day})"
    if level >= 2:
        count = len(analysis.action_items)
        if count > 1:
            return f"Needs you · {count} tasks"
        return "Needs a reply from you"
    return "FYI · nothing to do"


def build_notification(
    email: EmailSummaryItem, analysis: EmailAnalysis, level: int, *, today: date | None = None
) -> Notification:
    detail = analysis.risk_summary or analysis.summary or email.snippet or ""
    return Notification(
        sender=email.sender or "unknown sender",
        subject=email.subject or "(no subject)",
        verdict=verdict_line(analysis, level, today=today),
        detail=detail,
        level=level,
        message_id=email.id,
        url=get_settings().frontend_url,
    )


# --------------------------------------------------------------------------
# One cycle
# --------------------------------------------------------------------------


@dataclass
class ScanResult:
    checked: int = 0
    baselined: int = 0
    analysed: int = 0
    notified: int = 0
    ignored: int = 0
    failed: int = 0
    cached: int = 0

    def line(self) -> str:
        if self.baselined:
            return f"baseline: {self.baselined} message(s) recorded, no cards"
        return (
            f"checked={self.checked} analysed={self.analysed} cached={self.cached} "
            f"notified={self.notified} ignored={self.ignored} failed={self.failed}"
        )


def _account() -> str:
    try:
        return token_store.status().get("email") or ""
    except Exception:  # noqa: BLE001 - a missing account only weakens one filter
        return ""


def _default_fetch() -> list[EmailSummaryItem]:
    return gmail_client.list_emails(max_results=MAX_RESULTS, query=POLL_QUERY)


def _default_analyse(email: EmailSummaryItem) -> tuple[EmailAnalysis, ModelUsage | None]:
    detail = gmail_client.get_email(email.id)
    analysis, usage, _audit = analyzer.analyse(detail)
    return analysis, usage


def _sort_key(email: EmailSummaryItem) -> float:
    # Oldest first: a burst is processed in the order it arrived, so the mail
    # that has been waiting longest is the one that gets the card.
    try:
        return email.date.timestamp() if email.date else 0.0
    except (OSError, ValueError):
        return 0.0


def scan_once(
    *,
    notify: Callable[[Notification], None] | None = None,
    fetch: Callable[[], list[EmailSummaryItem]] | None = None,
    analyse: Callable[[EmailSummaryItem], tuple[EmailAnalysis, ModelUsage | None]] | None = None,
    now: datetime | None = None,
) -> ScanResult:
    """Run one polling cycle. Never raises: a watcher that dies is not a watcher.

    Dependencies are injectable so the interesting behaviour — filtering, the
    baseline, the cap, the seen-table — can be tested without Gmail or a model.
    """
    now = now or datetime.now(timezone.utc)
    fetch = fetch or _default_fetch
    analyse = analyse or _default_analyse
    result = ScanResult()

    try:
        emails = fetch()
    except Exception as exc:  # noqa: BLE001 - network, auth, quota
        log.warning("watcher.fetch_failed error=%s", type(exc).__name__)
        result.failed += 1
        return result

    result.checked = len(emails)

    # First run: record what is already there and say nothing. Starting the
    # watcher on an inbox with twenty unread messages must not fire twenty
    # cards; the user would uninstall it before the demo started.
    if not store.watcher_baselined():
        store.mark_seen([email.id for email in emails])
        store.mark_baselined()
        result.baselined = len(emails)
        log.info("watcher.baseline messages=%d", len(emails))
        return result

    seen = store.seen_ids([email.id for email in emails])
    fresh = sorted((email for email in emails if email.id not in seen), key=_sort_key)
    if not fresh:
        return result

    account = _account()
    for email in fresh[:MAX_PER_CYCLE]:
        if is_noise(email, account=account):
            store.mark_seen([email.id])
            result.ignored += 1
            log.info("watcher.ignored id=%s", email.id)
            continue

        try:
            hit = store.cache_lookup(email.id)
            if hit is not None:
                analysis = hit[0]
                result.cached += 1
            else:
                analysis, usage = analyse(email)
                # Persisted here rather than inside the analyser so that every
                # path through this loop leaves the UI with a warm cache: the
                # second click on an email costs nothing whether the watcher or
                # the user asked for it first.
                store.cache_store(analysis, usage, account=account)
                result.analysed += 1
        except Exception as exc:  # noqa: BLE001 - one bad message, not a dead loop
            # Marked seen anyway: a message that always fails would otherwise
            # block the queue forever. It stays uncached, so the user can still
            # analyse it by hand from the inbox.
            log.warning("watcher.analyse_failed id=%s error=%s", email.id, type(exc).__name__)
            store.mark_seen([email.id])
            result.failed += 1
            continue

        store.mark_seen([email.id])
        level = urgency(analysis, today=now.date())
        if level >= NOTIFY_FROM and notify is not None:
            try:
                notify(build_notification(email, analysis, level, today=now.date()))
                result.notified += 1
            except Exception as exc:  # noqa: BLE001 - a broken notifier is not fatal
                log.warning("watcher.notify_failed error=%s", type(exc).__name__)

    log.info("watcher.cycle %s", result.line())
    return result


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def run_forever(
    *,
    interval: float = DEFAULT_INTERVAL,
    notify: Callable[[Notification], None] | None = None,
    stop: threading.Event | None = None,
    fetch: Callable[[], list[EmailSummaryItem]] | None = None,
    analyse: Callable[[EmailSummaryItem], tuple[EmailAnalysis, ModelUsage | None]] | None = None,
) -> None:
    """Poll until `stop` is set.

    An exception in one cycle must not end the loop: the whole point of a
    background watcher is that it is still there in an hour.
    """
    stop = stop or threading.Event()
    log.info("watcher.start interval=%.0fs query=%r", interval, POLL_QUERY)
    while not stop.is_set():
        try:
            scan_once(notify=notify, fetch=fetch, analyse=analyse)
        except Exception:  # noqa: BLE001 - defensive: scan_once should not raise
            log.exception("watcher.cycle_crashed")
        stop.wait(interval)
    log.info("watcher.stop")


__all__ = [
    "MAX_PER_CYCLE",
    "NOTIFY_FROM",
    "POLL_QUERY",
    "ScanResult",
    "build_notification",
    "is_noise",
    "run_forever",
    "scan_once",
    "sender_address",
    "urgency",
    "verdict_line",
]
