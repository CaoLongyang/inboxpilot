"""The navigation is a top bar of st.pills, not a sidebar.

These tests exist because the navigation is now the only way to reach a page,
so a wrong page list or a lost selection is not a cosmetic bug: it is a dead
end. They boot the real frontend script with the backend pointed at a closed
port, which makes them hermetic and fast (connection refused, no model calls,
no Gmail).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"

PAGES = ["Connect", "Inbox", "Digest", "Analysis", "My week", "Action Review"]

# The heading each page renders once it is the current page.
HEADINGS = {
    "Connect": "### Connect Gmail",
    "Inbox": "### Inbox",
    "Digest": "### Digest",
    "Analysis": "### Analysis",
    "My week": "### My week",
    "Action Review": "### Review and approve",
}


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    """The frontend, booted with no backend behind it."""
    monkeypatch.setenv("INBOXPILOT_API_URL", "http://127.0.0.1:9")
    return AppTest.from_file(str(APP), default_timeout=60).run()


def nav(app: AppTest):
    groups = app.get("pills")
    assert len(groups) == 1, "expected exactly one navigation widget"
    return groups[0]


def markdown_text(app: AppTest) -> list[str]:
    return [block.value for block in app.markdown]


def test_the_app_boots_with_the_backend_down(app: AppTest) -> None:
    # A missing backend must degrade to a status badge, not a traceback: the
    # first thing a judge sees should never be a stack trace.
    assert not app.exception
    assert any("backend offline" in text for text in markdown_text(app))


def test_the_navigation_lists_every_page(app: AppTest) -> None:
    assert nav(app).options == PAGES


def test_the_app_opens_on_connect(app: AppTest) -> None:
    assert nav(app).value == "Connect"
    assert HEADINGS["Connect"] in markdown_text(app)


@pytest.mark.parametrize("page", PAGES[1:])
def test_every_page_can_be_reached(app: AppTest, page: str) -> None:
    nav(app).select(page)
    app.run()

    assert not app.exception, f"{page} raised"
    assert nav(app).value == page
    assert HEADINGS[page] in markdown_text(app)


def test_deselecting_the_nav_keeps_the_current_page(app: AppTest) -> None:
    # st.pills in single-selection mode can be clicked off. Without the
    # fallback in top_bar() that leaves a blank screen with no way back.
    nav(app).select("Action Review")
    app.run()
    nav(app).unselect("Action Review")
    app.run()

    assert not app.exception
    assert HEADINGS["Action Review"] in markdown_text(app)
