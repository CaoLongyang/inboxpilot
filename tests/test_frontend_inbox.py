"""The analysis renders inside the email's own card, not in a section below.

This was a real usability complaint: clicking Analyze on the fifth email put the
answer ten rows away from the question. These tests boot the real frontend
script against a stub backend, so they exercise the actual render path without
Gmail, without Nebius, and without spending a model call.

They also pin the two things that are easy to break while moving this code
around: the analysis must be reachable without re-analysing, and the old
bottom-of-page section must not come back.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import streamlit as st
import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"

EMAIL = {
    "id": "msg-1",
    "subject": "WIA2007 project proposal — submission reminder",
    "sender": "Dr. Norhayati <norhayati@example.edu>",
    "date": "2026-10-30T09:00:00+08:00",
    "snippet": "The proposal is due on Saturday, 31 October 2026 at 23:59.",
    "unread": True,
    "labels": ["INBOX", "UNREAD"],
}

ANALYSIS = {
    "analysis": {
        "category": "action_required",
        "action_required": True,
        "summary": "A lecturer reminds the class that the WIA2007 proposal is due on 31 October.",
        "key_facts": [
            {
                "text": "The proposal is due on 31 October 2026 at 23:59.",
                "kind": "explicit",
                "evidence": [{"quote": "due on Saturday, 31 October 2026 at 23:59"}],
            }
        ],
        "action_items": [{"title": "Submit the WIA2007 proposal"}],
        "uncertainties": [],
        "model": "nvidia/nemotron-3-super-120b-a12b",
    },
    "usage": {"latency_ms": 1088, "total_tokens": 81},
    "cached": True,
}


class _Stub(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep pytest output clean
        return

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/health":
            self._json({"status": "ok", "email_send_enabled": False})
        elif path == "/auth/status":
            self._json(
                {
                    "client_configured": True,
                    "connected": True,
                    "email": "demo@example.com",
                    "scopes": ["gmail.readonly", "gmail.compose"],
                }
            )
        elif path == "/emails":
            self._json({"emails": [EMAIL]})
        elif path == "/analyses/msg-1":
            self._json(ANALYSIS)
        else:
            self._json({})


@pytest.fixture(scope="module")
def stub_url():
    """One stub for the whole module, on purpose.

    `api_request` keeps its httpx client in `st.cache_resource`, which is
    process-global and outlives a single AppTest. A per-test server would leave
    the cached client pointed at a closed port, and every test after the first
    would render an empty inbox.
    """
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    previous = os.environ.get("INBOXPILOT_API_URL")
    os.environ["INBOXPILOT_API_URL"] = f"http://127.0.0.1:{server.server_port}"
    try:
        yield os.environ["INBOXPILOT_API_URL"]
    finally:
        if previous is None:
            os.environ.pop("INBOXPILOT_API_URL", None)
        else:
            os.environ["INBOXPILOT_API_URL"] = previous
        server.shutdown()
        server.server_close()
        # The cached httpx client still points at the port we just closed, and
        # the caches are process-global: leave them and the next test module
        # talks to a dead socket.
        st.cache_data.clear()
        st.cache_resource.clear()


@pytest.fixture
def inbox(stub_url: str):
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    app.get("pills")[0].select("Inbox")
    app.run()
    return app


def labels(app: AppTest) -> list[str]:
    return [button.label for button in app.button]


def markdown_text(app: AppTest) -> str:
    return "\n".join(block.value for block in app.markdown)


def test_the_analysis_is_attached_to_its_email(inbox: AppTest) -> None:
    assert not inbox.exception
    # The card offers to open the analysis that already exists — it does not ask
    # the user to analyse the email a second time.
    assert "Show analysis" in labels(inbox)
    assert "Re-analyze" in labels(inbox)


def test_the_analysis_starts_folded_but_announces_itself(inbox: AppTest) -> None:
    text = markdown_text(inbox)
    # Folded, but the reader still learns what it is without opening it.
    assert "1 key facts" in text or "key facts" in text
    assert "action_required" not in text, "raw enum leaked into the page"
    assert "Action required" in text


def test_opening_the_analysis_shows_it_in_place(inbox: AppTest) -> None:
    button = next(b for b in inbox.button if b.label == "Show analysis")
    button.click()
    inbox.run()

    assert not inbox.exception
    assert "Hide analysis" in labels(inbox)
    text = markdown_text(inbox)
    # The section label is "Summary" in the DOM and uppercased by CSS, so the
    # assertion matches the markup rather than what the stylesheet renders.
    assert ">Summary<" in text
    assert ">Action items<" in text
    assert "WIA2007 proposal is due on 31 October" in text
    assert "Submit the WIA2007 proposal" in text


def test_the_old_separate_results_section_is_gone(inbox: AppTest) -> None:
    # The complaint this feature answers: results listed far away from the row
    # that produced them.
    assert "### Analysis results" not in markdown_text(inbox)
