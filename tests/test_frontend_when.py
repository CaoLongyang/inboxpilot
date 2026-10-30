"""The inbox date formatter, which is where timezones go to be wrong.

Found while seeding demo mail: Gmail stores a message sent from Malaysia to an
account configured for US Pacific as `Fri, 30 Oct 2026 01:43:10 -0700`. The
instant is correct — 16:43 in Kuala Lumpur — but the formatter printed the
sender's wall clock, so today's mail appeared to have arrived at 1am.

These tests pin the conversion, not the machine's timezone: the expected value
is computed the same way the function should, and the guard test skips on a
machine that genuinely is at -07:00.
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.app import _when  # noqa: E402

PACIFIC = "2026-10-30T01:43:10-07:00"


def test_converts_to_the_readers_clock() -> None:
    expected = dt.datetime.fromisoformat(PACIFIC).astimezone().strftime(
        "%d %b %Y, %H:%M"
    )
    assert _when({"date": PACIFIC}) == expected


def test_does_not_print_the_senders_wall_clock() -> None:
    """The actual bug. On any machine that is not at -07:00, 01:43 is wrong."""
    local_offset = dt.datetime.fromisoformat(PACIFIC).astimezone().utcoffset()
    if local_offset == dt.timedelta(hours=-7):
        return  # nothing to prove on a Pacific machine
    assert _when({"date": PACIFIC}) != "30 Oct 2026, 01:43"


def test_accepts_a_z_suffix() -> None:
    expected = dt.datetime.fromisoformat("2026-10-29T08:00:00+00:00").astimezone()
    assert _when({"date": "2026-10-29T08:00:00Z"}) == expected.strftime(
        "%d %b %Y, %H:%M"
    )


def test_missing_date_is_named_not_guessed() -> None:
    assert _when({}) == "unknown date"
    assert _when({"date": ""}) == "unknown date"


def test_unparseable_date_falls_back_to_the_raw_string() -> None:
    assert _when({"date": "sometime last tuesday"}) == "sometime last tu"
