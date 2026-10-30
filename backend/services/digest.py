"""A week of inbox, reduced to one answer.

The naive version of this feature reads every message in full and asks a model
to summarise them. On a normal week that is a hundred thousand tokens and a
hundred inference calls, which is slow enough that nobody would open the page
twice. So the input is built from two tiers of evidence instead:

- **Analysed messages** contribute structured facts — the summary, the tasks,
  the resolved deadlines. Those are free: the watcher and the analysis page
  already paid for them, and they are sitting in `analysis_results`.
- **Everything else** contributes only what `messages.list` already returned:
  sender, subject, date. No extra Gmail call, no inference.

One aggregation call then reads the whole week. The digest reports how many
messages fell into each tier, because "twelve of these I actually read" is the
difference between a summary and a guess, and the reader deserves to know which
one they are looking at.

What this module refuses to do: invent. The model is given dates, senders and
tasks that already exist and is asked to arrange them, not to reason about what
might be in an email it never saw. A deadline that is not in the input cannot
appear in the output, because the prompt has nothing to copy it from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from backend.schemas import (
    DigestDeadline,
    EmailAnalysis,
    EmailSummaryItem,
    WeeklyDigest,
)
from backend.services import gmail_client, nebius_client, store

log = logging.getLogger(__name__)

DEFAULT_DAYS = 7
# A week of a busy inbox is 60-100 messages. Sixty is enough to characterise a
# week and keeps the prompt near 8k tokens; beyond that the marginal message
# changes nothing but the bill.
MAX_MESSAGES = 60
MAX_DEADLINES = 10
MAX_NEEDS_YOU = 5
MAX_IGNORED_CHARS = 300

SYSTEM = """You are summarising one week of a person's inbox for that person.

You get one line per conversation. A line marked ANALYSED carries facts already
extracted from that email by another model: its summary, its tasks and any
resolved deadline. A line marked METADATA carries only the subject and sender.

Reply with JSON only:
{
  "headline": "one sentence, at most 20 words: what this week actually demanded",
  "deadlines": [{"date": "YYYY-MM-DD", "what": "what is due", "source": "subject line"}],
  "needs_you": [3, 1],
  "ignored": "one sentence describing what can be skipped, with a count",
  "quiet": false
}

Rules:
- Use only dates, senders and tasks present in the input. If a line has no
  deadline, it contributes no deadline. Never guess a date.
- deadlines: sorted ascending, at most ten. Empty list if nothing is due.
- needs_you: a list of CANDIDATE TASK ids, most important first, at most five.
  Only ids from the candidate list at the end of the input. Do not write your
  own task text: if something feels missing, it is not in this week's mail.
  If two candidates are the same task, pick one. An empty list is a good answer.
- Do not list every email. Group them: "47 newsletters" is one clause, not 47.
- If nothing needed action, set "quiet": true and say so plainly. Do not invent
  importance to fill the space.
"""


@dataclass
class Week:
    """What the digest is built from, and what it cost to build."""

    days: int = DEFAULT_DAYS
    messages: list[EmailSummaryItem] = field(default_factory=list)
    analyses: dict[str, EmailAnalysis] = field(default_factory=dict)

    @property
    def analysed(self) -> int:
        return sum(1 for message in self.messages if message.id in self.analyses)

    @property
    def metadata_only(self) -> int:
        return len(self.messages) - self.analysed


def _one_line(message: EmailSummaryItem, analysis: EmailAnalysis | None) -> str:
    """The compact form the model reads. One conversation per line."""
    when = message.date.strftime("%Y-%m-%d") if message.date else "unknown-date"
    sender = " ".join((message.sender or "unknown sender").split())[:60]
    subject = " ".join((message.subject or "(no subject)").split())[:90]
    if analysis is None:
        return f"{when} | {sender} | {subject} | METADATA"

    due = [
        item.deadline.date.isoformat()
        for item in analysis.action_items
        if item.deadline is not None and item.deadline.date is not None
    ]
    tasks = "; ".join(item.title for item in analysis.action_items[:3]) or "none"
    summary = " ".join((analysis.summary or "").split())[:140]
    flag = "action required" if analysis.action_required else "no action"
    return (
        f"{when} | {sender} | {subject} | ANALYSED: {summary}"
        f" | tasks: {tasks} | {flag}"
        + (f" | due: {', '.join(sorted(set(due)))}" if due else "")
    )


def candidates(week: Week) -> list[dict[str, str]]:
    """Every concrete task already extracted from this week's mail.

    The model is not allowed to write task text; it chooses among these. That
    is the difference between a digest that pads itself with plausible advice
    ("confirm readiness", "review your settings") and one where every line
    traces back to a sentence someone actually sent.
    """
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for message in week.messages:
        analysis = week.analyses.get(message.id)
        if analysis is None:
            continue
        for item in analysis.action_items:
            title = " ".join((item.title or "").split())
            key = title.casefold()
            if not title or key in seen:
                continue
            seen.add(key)
            found.append({"title": title[:200], "source": message.subject[:120]})
    return found


def build_messages(week: Week) -> list[dict[str, str]]:
    lines = [_one_line(message, week.analyses.get(message.id)) for message in week.messages]
    body = "\n".join(lines) if lines else "(no messages)"

    tasks = candidates(week)
    if tasks:
        listing = "\n".join(
            f"[{index}] {task['title']} | from: {task['source']}"
            for index, task in enumerate(tasks, start=1)
        )
        task_block = f"\n\nCANDIDATE TASKS (needs_you refers to these ids):\n{listing}"
    else:
        task_block = "\n\nCANDIDATE TASKS: none. needs_you must be an empty list."

    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                f"The last {week.days} days, newest first. "
                f"{len(week.messages)} conversations, {week.analysed} analysed in full, "
                f"{week.metadata_only} with metadata only.\n\n{body}{task_block}"
            ),
        },
    ]


def collect(days: int = DEFAULT_DAYS) -> Week:
    """Read the window and attach whatever analyses already exist.

    One conversation per line: a thread that went back and forth eight times is
    one thing that happened, and repeating it eight times would let a chatty
    thread dominate the summary.
    """
    messages = gmail_client.list_emails_up_to(f"in:inbox newer_than:{days}d", MAX_MESSAGES)

    seen_threads: set[str] = set()
    unique: list[EmailSummaryItem] = []
    for message in messages:  # newest first
        key = message.thread_id or message.id
        if key in seen_threads:
            continue
        seen_threads.add(key)
        unique.append(message)

    week = Week(days=days, messages=unique)
    for message in unique:
        hit = store.cache_lookup(message.id)
        if hit is not None:
            week.analyses[message.id] = hit[0]
    log.info(
        "digest.collected days=%d messages=%d analysed=%d",
        days, len(unique), week.analysed,
    )
    return week


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _fallback_headline(week: Week) -> str:
    if not week.messages:
        return "No mail in this window."
    return f"{len(week.messages)} conversations, none of them urgent."


def _resolve_task(entry: object, options: list[dict[str, str]]) -> str:
    """Map the model's answer back to a task we actually extracted.

    Accepts the id it was asked for, and tolerates an exact title if it wrote
    one anyway. Anything else is dropped: an unverifiable task is exactly the
    kind of invented to-do this design exists to prevent.
    """
    if isinstance(entry, bool):
        return ""
    if isinstance(entry, int) or (isinstance(entry, str) and entry.strip().isdigit()):
        index = int(entry)
        if 1 <= index <= len(options):
            return options[index - 1]["title"]
        log.info("digest.task_out_of_range id=%s options=%d", index, len(options))
        return ""
    if isinstance(entry, str):
        wanted = " ".join(entry.split()).casefold()
        for option in options:
            if option["title"].casefold() == wanted:
                return option["title"]
        log.info("digest.task_unmatched text=%r", entry[:60])
    return ""


def parse(raw: dict, week: Week, model: str) -> WeeklyDigest:
    """Turn the model's JSON into a digest, dropping anything unusable.

    Salvage rather than reject: one malformed deadline should cost that
    deadline, not the whole answer the user waited ten seconds for.
    """
    headline = " ".join(str(raw.get("headline") or "").split())[:300]
    if not headline:
        headline = _fallback_headline(week)

    deadlines: list[DigestDeadline] = []
    for entry in raw.get("deadlines") or []:
        if not isinstance(entry, dict):
            continue
        when = _parse_date(entry.get("date"))
        what = " ".join(str(entry.get("what") or "").split())[:200]
        if when is None or not what:
            log.info("digest.deadline_dropped what=%r date=%r", what[:40], entry.get("date"))
            continue
        deadlines.append(
            DigestDeadline(
                date=when,
                what=what,
                source=" ".join(str(entry.get("source") or "").split())[:200],
            )
        )
    deadlines.sort(key=lambda item: item.date)

    needs: list[str] = []
    options = candidates(week)
    for entry in raw.get("needs_you") or []:
        title = _resolve_task(entry, options)
        if title and title not in needs:
            needs.append(title)

    ignored = " ".join(str(raw.get("ignored") or "").split())[:MAX_IGNORED_CHARS]

    return WeeklyDigest(
        headline=headline,
        deadlines=deadlines[:MAX_DEADLINES],
        needs_you=needs[:MAX_NEEDS_YOU],
        ignored=ignored,
        # "Quiet" is a claim about the week, so it is computed here from what
        # survived validation. Trusting the model's own flag would let it call a
        # week quiet after inventing three tasks, or busy after inventing none.
        quiet=not needs and not deadlines,
        days=week.days,
        covered=len(week.messages),
        analysed=week.analysed,
        metadata_only=week.metadata_only,
        model=model,
    )


def generate(days: int = DEFAULT_DAYS, *, week: Week | None = None) -> WeeklyDigest:
    """Build a digest for the window. Raises UpstreamError if the model fails."""
    week = week or collect(days)

    # An empty window must not cost a call, and must not produce a model
    # invention. This is also the honest answer: there was no mail.
    if not week.messages:
        return WeeklyDigest(
            headline=_fallback_headline(week),
            quiet=True,
            days=week.days,
            covered=0,
        )

    raw, usage = nebius_client.chat_json(
        build_messages(week),
        role="reasoning",
        max_tokens=1200,
        thinking=False,
    )
    digest = parse(raw, week, model=usage.model)
    store.save_digest(digest)
    log.info(
        "digest.generated days=%d covered=%d deadlines=%d needs=%d tokens=%s",
        days, digest.covered, len(digest.deadlines), len(digest.needs_you),
        usage.total_tokens,
    )
    return digest


__all__ = [
    "DEFAULT_DAYS",
    "MAX_MESSAGES",
    "Week",
    "build_messages",
    "collect",
    "generate",
    "parse",
]
