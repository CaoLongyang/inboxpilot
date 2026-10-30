"""Card rendering, without a display.

The Tk window itself is not tested here: opening windows in a test suite is
how you get flaky builds and stray processes. What is tested is the text that
goes on the card, which is the part that can be wrong in a way a human notices,
and the fact that a headless machine still gets the information.
"""

from __future__ import annotations

import pytest

from backend.services import notify


def card(**overrides) -> notify.Notification:
    base = dict(
        sender="Dr Lim <lim@um.edu.my>",
        subject="Thesis chapter 2 submission",
        verdict="Due tomorrow (Oct 31)",
        detail="Late submission loses 10% per day.",
        level=3,
        message_id="m1",
    )
    base.update(overrides)
    return notify.Notification(**base)


def test_the_console_form_shows_the_verdict_not_just_the_subject() -> None:
    text = notify.render_text(card())

    assert "Due tomorrow" in text
    assert "Thesis chapter 2 submission" in text
    assert "Dr Lim" in text


def test_long_text_is_truncated_rather_than_wrapping_forever() -> None:
    text = notify.render_text(card(subject="x" * 400))

    assert "…" in text
    assert all(len(line) < 140 for line in text.splitlines())


def test_newlines_in_a_subject_cannot_forge_a_second_card() -> None:
    # Subject lines are attacker-controlled. A newline would otherwise let a
    # sender add what looks like another notification to the console form.
    text = notify.render_text(card(subject="Hello\n[3] CEO <ceo@bank>"))

    assert len(text.splitlines()) <= 4
    assert "\n[3]" not in text


def test_a_card_is_immutable() -> None:
    with pytest.raises(Exception):
        card().subject = "changed"  # type: ignore[misc]


def test_without_a_desktop_we_still_get_a_notifier(capsys: pytest.CaptureFixture[str]) -> None:
    notifier = notify.make_notifier(desktop=False)
    assert isinstance(notifier, notify.ConsoleNotifier)

    notifier.show(card())
    captured = capsys.readouterr()

    assert "Due tomorrow" in captured.out


def test_a_failed_desktop_notifier_falls_back_instead_of_dropping_the_card(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Simulates a machine where Tk cannot start: the watcher must keep working.
    notifier = notify.TkNotifier()
    notifier._failed = True

    notifier.show(card(verdict="Needs a reply from you"))
    captured = capsys.readouterr()

    assert "Needs a reply from you" in captured.out
