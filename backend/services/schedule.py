"""Does an invitation actually fit the week the user described?

Why the arithmetic is here and not in the prompt
------------------------------------------------
Asking a model "does this clash with my timetable?" produces a fluent answer
that is occasionally wrong, and there is no way to tell which. Asking it "when
does this event happen?" is extraction, which is what it is good at. The step
after that — do two intervals overlap — is a comparison a computer gets right
every single time, and whose answer we can show the user as evidence
("Tue 09:00-11:00 lecture").

So the model extracts, this module decides, and the verdict is explainable.

Recurring weekly blocks, not a calendar import
----------------------------------------------
The user answers "when are you normally busy?" once, in thirty seconds, and we
never ask for a calendar scope we do not need. The cost is that one-off
appointments are invisible; that is a deliberate trade for a demo that has to
be honest about what it knows.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from backend.schemas import BusyBlock, Conflict, ScheduleProfile

# A window longer than this is not an event any more, it is a date range typed
# into the wrong field. Scanning it would produce hundreds of "conflicts".
MAX_WINDOW_DAYS = 90

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_hhmm(value: str) -> time:
    """`"09:30"` -> `time(9, 30)`. The schema already restricts the format."""
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _occurrence(block: BusyBlock, on: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(on, parse_hhmm(block.start)),
        datetime.combine(on, parse_hhmm(block.end)),
    )


def find_conflicts(
    profile: ScheduleProfile, start: datetime | None, end: datetime | None
) -> list[Conflict]:
    """Every busy block the event window overlaps, one entry per occurrence.

    An event that spans several days yields one conflict per clashing day, so
    "a three-day hackathon that eats two lectures" is visible as two rows
    rather than one vague warning.
    """
    if start is None or end is None or end <= start:
        return []

    conflicts: list[Conflict] = []
    last_day = min(end.date(), start.date() + timedelta(days=MAX_WINDOW_DAYS))
    day = start.date()
    while day <= last_day:
        for block in profile.busy_blocks:
            if block.weekday != day.weekday():
                continue
            busy_start, busy_end = _occurrence(block, day)
            if busy_start < end and start < busy_end:
                conflicts.append(
                    Conflict(
                        busy_label=block.label or "busy",
                        weekday=block.weekday,
                        on_date=day,
                        start=max(busy_start, start),
                        end=min(busy_end, end),
                    )
                )
        day += timedelta(days=1)
    conflicts.sort(key=lambda c: c.start)
    return conflicts


def summarise(profile: ScheduleProfile) -> str:
    """The timetable as the model sees it: one line per block, plus notes."""
    if not profile.busy_blocks:
        lines = ["(the user has not described their week)"]
    else:
        ordered = sorted(profile.busy_blocks, key=lambda b: (b.weekday, b.start))
        lines = [
            f"{WEEKDAYS[b.weekday]} {b.start}-{b.end}"
            + (f" {b.label}" if b.label else "")
            for b in ordered
        ]
    text = "\n".join(lines)
    if profile.notes.strip():
        text += f"\nNOTES FROM THE USER: {profile.notes.strip()}"
    return text


def describe_conflict(conflict: Conflict) -> str:
    """One clash, in words a person can check against their own week."""
    day = WEEKDAYS[conflict.weekday]
    return (
        f"{day} {conflict.on_date.isoformat()} "
        f"{conflict.start.strftime('%H:%M')}-{conflict.end.strftime('%H:%M')}"
        + (f" ({conflict.busy_label})" if conflict.busy_label else "")
    )


__all__ = [
    "MAX_WINDOW_DAYS",
    "WEEKDAYS",
    "describe_conflict",
    "find_conflicts",
    "parse_hhmm",
    "summarise",
]
