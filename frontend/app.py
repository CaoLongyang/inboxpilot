"""InboxPilot Streamlit frontend.

Owner: B — Product Experience / Agent UX.
Uses the existing FastAPI contract in backend/schemas.py; no direct Gmail
or Nebius calls, and no OAuth or model secrets in the browser.

Start:
    python -m streamlit run frontend/app.py
Backend:
    python -m uvicorn backend.main:app --reload --port 8000

Screen model
------------
Inbox is a list, not a wizard. Ten emails load at once, each row shows enough of
the message to decide about it, and every row carries its own Analyze button.
A checkbox column plus a batch bar covers "analyze several"; nothing forces the
user through a page per email.

Analysis results stay attached to the row that produced them, so a second pass
over the same list never loses the first answer. The deep-dive page still exists
for one email at a time, because that is where a reply gets proposed and
approved.
"""
from __future__ import annotations

import html
import os
import uuid
from typing import Any

import httpx
import streamlit as st


API_BASE = os.getenv("INBOXPILOT_API_URL", "http://localhost:8000").rstrip("/")
PAGES = ["Connect", "Inbox", "Analysis", "Action Review"]
PAGE_SIZES = [10, 20, 30]
DEFAULT_PAGE_SIZE = 10


# --------------------------------------------------------------------------
# API layer
# --------------------------------------------------------------------------


class APIError(Exception):
    def __init__(self, message: str, code: str = "api_error"):
        super().__init__(message)
        self.code = code


@st.cache_resource(show_spinner=False)
def _http_client() -> httpx.Client:
    """One pooled HTTP client for the whole Streamlit process.

    Both arguments here are measured, not stylistic. Against the local backend:

    * ``httpx.request()`` (the obvious call) builds a new connection pool and
      re-reads the environment on **every** call: ~190 ms per request here.
      The same request through a shared client: ~2 ms.
    * ``trust_env=False`` keeps the machine's ``http_proxy`` out of localhost
      traffic. The proxy accepts the connection and then fails to reach
      ``127.0.0.1``, which costs a round trip before falling back.

    Streamlit reruns the whole script on every widget interaction, so a
    sidebar health check plus an auth check is two of these per click. That is
    what "laggy page switching" actually was.
    """
    return httpx.Client(
        base_url=API_BASE,
        timeout=httpx.Timeout(15.0, connect=5.0),
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=15.0),
        headers={"User-Agent": "InboxPilot-frontend/1.0"},
    )


def api_request(
    method: str, path: str, *, params: dict | None = None,
    payload: dict | None = None, timeout: float = 15.0,
) -> dict[str, Any]:
    """Call the backend; respect its documented error envelope."""
    client = _http_client()
    try:
        try:
            response = client.request(
                method, path, params=params, json=payload, timeout=timeout,
            )
        except httpx.RemoteProtocolError:
            # The pooled keep-alive socket died under us (backend restarted).
            # One retry on a fresh connection; anything else is a real failure.
            response = client.request(
                method, path, params=params, json=payload, timeout=timeout,
            )
    except httpx.RequestError as exc:
        raise APIError(f"Cannot contact backend at {API_BASE}. Is FastAPI running?") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise APIError(f"Backend returned HTTP {response.status_code} without JSON.") from exc

    if response.is_error:
        err = data.get("error", {}) if isinstance(data, dict) else {}
        code = err.get("code", "http_error")
        message = err.get("message", f"HTTP {response.status_code}")
        raise APIError(message, code)
    if not isinstance(data, dict):
        raise APIError("Unexpected backend response format.")
    return data


def error_message(exc: APIError) -> None:
    if exc.code == "not_authenticated":
        st.warning(f"Gmail authorization required: {exc}")
    elif exc.code == "approval_required":
        st.warning(f"Blocked by the approval gate: {exc}")
    elif exc.code == "configuration_error":
        st.error(f"Backend is missing configuration: {exc}")
    else:
        st.error(f"{exc.code}: {exc}")


@st.cache_data(ttl=20, show_spinner=False)
def _health_snapshot() -> dict[str, Any]:
    """Cached health check for the sidebar badge.

    The sidebar is drawn on every rerun, so an uncached call here meant one
    network round trip per click. Failures are cached too — otherwise an
    offline backend would pay the connect timeout on every single rerun.
    """
    try:
        return {"ok": True, "data": api_request("GET", "/health", timeout=3.0)}
    except APIError as exc:
        return {"ok": False, "error": str(exc)}


@st.cache_data(ttl=15, show_spinner=False)
def _auth_status() -> dict[str, Any]:
    """Cached Gmail connection state.

    Cached for 15 s, and explicitly invalidated by the actions that change it
    (connect, disconnect, refresh). A stale badge after OAuth is the one thing
    that would be worse than the latency, hence the short TTL and the button.
    """
    try:
        return api_request("GET", "/auth/status", timeout=5.0)
    except APIError as exc:
        return {"connected": False, "error": str(exc), "failed": True}


def forget_status_cache() -> None:
    """Drop cached status after anything that could change it."""
    _auth_status.clear()
    _health_snapshot.clear()


# --------------------------------------------------------------------------
# Presentation helpers
# --------------------------------------------------------------------------


def _theme() -> str:
    try:
        return st.context.theme.type or "dark"
    except Exception:  # noqa: BLE001 - theme is cosmetic, never fatal
        return "dark"


_PALETTE = {
    "dark": {
        "green": ("rgba(63,185,80,.15)", "#3fb950", "rgba(63,185,80,.4)"),
        "amber": ("rgba(210,153,34,.15)", "#d29922", "rgba(210,153,34,.4)"),
        "blue": ("rgba(88,166,255,.15)", "#58a6ff", "rgba(88,166,255,.4)"),
        "gray": ("rgba(139,148,158,.15)", "#8b949e", "rgba(139,148,158,.35)"),
        "red": ("rgba(248,81,73,.15)", "#f85149", "rgba(248,81,73,.4)"),
        "muted": "#8b949e",
        "quote_bg": "rgba(139,148,158,.10)",
        "quote_bar": "rgba(139,148,158,.45)",
        "panel_bg": "rgba(139,148,158,.13)",
        "summary_bg": "rgba(88,166,255,.07)",
        "summary_bar": "#58a6ff",
        "task_bg": "rgba(210,153,34,.10)",
        "task_bar": "#d29922",
    },
    "light": {
        "green": ("rgba(26,127,55,.10)", "#1a7f37", "rgba(26,127,55,.35)"),
        "amber": ("rgba(154,103,0,.10)", "#9a6700", "rgba(154,103,0,.35)"),
        "blue": ("rgba(9,105,218,.10)", "#0969da", "rgba(9,105,218,.35)"),
        "gray": ("rgba(87,96,106,.10)", "#57606a", "rgba(87,96,106,.30)"),
        "red": ("rgba(207,34,46,.10)", "#cf222e", "rgba(207,34,46,.35)"),
        "muted": "#57606a",
        "quote_bg": "rgba(87,96,106,.07)",
        "quote_bar": "rgba(87,96,106,.35)",
        "panel_bg": "rgba(87,96,106,.09)",
        "summary_bg": "rgba(9,105,218,.05)",
        "summary_bar": "#0969da",
        "task_bg": "rgba(154,103,0,.08)",
        "task_bar": "#9a6700",
    },
}


def inject_css() -> None:
    p = _PALETTE[_theme()]
    st.markdown(
        f"""
<style>
  .block-container {{ padding-top: 2.0rem; padding-bottom: 3rem; max-width: 1200px; }}
  .ip-subject {{ font-size: 0.98rem; font-weight: 600; line-height: 1.35; margin: 0 0 2px 0; }}
  .ip-meta {{ font-size: 0.78rem; color: {p['muted']}; margin: 0 0 6px 0; }}
  .ip-snippet {{ font-size: 0.86rem; color: {p['muted']}; line-height: 1.45;
                 margin: 0; display: -webkit-box; -webkit-line-clamp: 2;
                 -webkit-box-orient: vertical; overflow: hidden; }}
  .ip-dot {{ color: #2f81f7; margin-right: 4px; }}
  .ip-badge {{ display: inline-block; padding: 2px 10px; border-radius: 999px;
               font-size: 0.76rem; font-weight: 600; letter-spacing: .02em;
               border: 1px solid transparent; white-space: nowrap; }}
  .ip-quote {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
               font-size: 0.85rem; line-height: 1.55; white-space: pre-wrap;
               background: {p['quote_bg']}; border-left: 3px solid {p['quote_bar']};
               padding: 9px 13px; border-radius: 6px; margin: 6px 0 12px 0; }}
  /* Section headings are bands, not captions: a tinted strip with an accent
     bar. At 0.74rem grey text they read as fine print and the sections
     blurred into one another. */
  .ip-label {{ font-size: 0.92rem; text-transform: uppercase; letter-spacing: .08em;
               color: inherit; margin: 26px 0 10px 0; font-weight: 800;
               padding: 6px 12px; background: {p['panel_bg']};
               border-left: 4px solid {p['quote_bar']};
               border-radius: 0 6px 6px 0; }}
  .ip-empty {{ font-size: 0.95rem; color: {p['muted']}; font-style: italic; }}

  /* The summary is the headline of the analysis: blue spine, its own tint,
     and larger than anything else on the page. */
  .ip-summary {{ font-size: 1.3rem; line-height: 1.75; padding: 16px 20px;
                 background: {p['summary_bg']};
                 border-left: 4px solid {p['summary_bar']};
                 border-radius: 0 10px 10px 0; margin: 0 0 4px 0; }}
  .ip-fact {{ display: flex; gap: 11px; align-items: baseline;
              font-size: 1.12rem; line-height: 1.6; padding: 8px 0; }}
  .ip-fact-text {{ flex: 1; }}

  /* A to-do is the point of the product, so it is the loudest element:
     amber spine, its own background, title larger than body text. */
  .ip-task {{ border-left: 4px solid {p['task_bar']}; background: {p['task_bg']};
              border-radius: 0 10px 10px 0; padding: 16px 20px; margin: 0 0 12px 0; }}
  .ip-task-title {{ font-size: 1.25rem; font-weight: 700; line-height: 1.45; }}
  .ip-task-why {{ font-size: 1rem; line-height: 1.6; color: {p['muted']};
                  margin-top: 5px; }}
  .ip-task-meta {{ font-size: 0.98rem; font-weight: 600; margin-top: 9px; }}
  .ip-task-meta span {{ font-weight: 400; color: {p['muted']}; }}
  .ip-task-note {{ font-size: 0.93rem; line-height: 1.55; color: {p['muted']};
                   margin-top: 6px; }}
</style>
""",
        unsafe_allow_html=True,
    )


def badge(text: str, tone: str = "gray") -> str:
    bg, fg, border = _PALETTE[_theme()][tone]
    return (
        f'<span class="ip-badge" style="background:{bg};color:{fg};'
        f'border-color:{border}">{html.escape(text)}</span>'
    )


def quote_block(text: str) -> str:
    return f'<div class="ip-quote">{html.escape(text or "")}</div>'


def esc(value: Any) -> str:
    """Escape for HTML, undoing any entity escaping that is already there.

    Gmail returns snippets and subjects with entities baked in (``Gmail -&gt;
    Drafts``), and the model sometimes copies them. Escaping those directly
    double-escapes them, so the user reads ``&gt;`` on screen. Unescaping first
    and escaping once is idempotent and still neutralises real markup.
    """
    return html.escape(html.unescape(str(value or "")))


def label(text: str, accent: str = "neutral") -> str:
    """Section heading: readable, not a whisper.

    ``accent="amber"`` ties the "Action items" heading to the amber spine of the
    task cards below it, so the eye can find the to-do section without reading.
    """
    palette = _PALETTE[_theme()]
    if accent == "amber":
        style = f"border-left-color:{palette['task_bar']};background:{palette['task_bg']}"
    else:
        style = f"border-left-color:{palette['quote_bar']}"
    return f'<div class="ip-label" style="{style}">{esc(text)}</div>'


def chips(
    category: str, needs_action: bool,
    facts: int | None = None, actions: int | None = None,
) -> str:
    """Badge row for one analysis.

    The category badge can already say "Action required", and the status badge
    says the same thing — two identical pills side by side read as a bug. Only
    add the status badge when the category has not already said it.
    """
    label_text, tone = _category_tone(category)
    parts = [badge(label_text, tone)]
    status = "action required" if needs_action else "no action needed"
    implied = {"action_required": "action required",
               "information_only": "no action needed"}.get(category)
    if implied != status:
        parts.append(badge(status, "amber" if needs_action else "gray"))
    if facts is not None:
        parts.append(badge(f"{facts} facts", "gray"))
    if actions is not None:
        parts.append(badge(f"{actions} action items", "gray"))
    return " ".join(parts)


def summary_block(text: str) -> str:
    """The summary is the headline of the analysis, so it renders as a panel."""
    return f'<div class="ip-summary">{esc(text)}</div>'


def fact_block(fact: dict) -> str:
    """One key fact: kind badge plus the claim, at body size."""
    kind = (fact.get("kind") or "unknown").upper()
    tone = {"EXPLICIT": "green", "INFERENCE": "amber"}.get(kind, "gray")
    return (
        f'<div class="ip-fact">{badge(kind.lower(), tone)}'
        f'<span class="ip-fact-text">{esc(fact.get("text"))}</span></div>'
    )


def task_block(action: dict) -> str:
    """One action item as a card.

    The to-do is the reason the product exists, so it is the loudest thing on
    the page: an amber spine, its own background, a title at body size. It used
    to be a bold sentence followed by two captions, which read as a footnote.
    """
    parts = ['<div class="ip-task">']
    parts.append(f'<div class="ip-task-title">{esc(action.get("title") or "Untitled")}</div>')
    if action.get("why"):
        parts.append(f'<div class="ip-task-why">{esc(action["why"])}</div>')

    deadline = action.get("deadline") or {}
    stamp = deadline.get("date") or deadline.get("text")
    if stamp:
        kind = deadline.get("kind") or "unknown"
        parts.append(
            f'<div class="ip-task-meta">Due {esc(stamp)} <span>({esc(kind)})</span></div>'
        )
    if action.get("risk"):
        parts.append(f'<div class="ip-task-note">If ignored: {esc(action["risk"])}</div>')
    needs = action.get("needs_user_input") or []
    if needs:
        parts.append(
            f'<div class="ip-task-note">Needs your input: {esc("; ".join(needs))}</div>'
        )
    parts.append("</div>")
    return "".join(parts)


def go(page: str) -> None:
    st.session_state.requested_page = page
    st.rerun()


def clear_email_results() -> None:
    for name in (
        "analysis_response", "plan_response", "selected_proposal_id",
        "submitted_payload", "execution_result", "analyses", "email_list",
        "message_id",
    ):
        st.session_state.pop(name, None)
    for key in [k for k in st.session_state if str(k).startswith("sel_")]:
        st.session_state.pop(key, None)


def _sender(item: dict) -> str:
    return item.get("from") or item.get("sender") or "Unknown sender"


def _when(item: dict) -> str:
    raw = item.get("date") or ""
    if not raw:
        return "unknown date"
    # "2026-10-29T15:30:31+08:00" -> "29 Oct 2026, 15:30"
    try:
        from datetime import datetime

        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed.strftime("%d %b %Y, %H:%M")
    except ValueError:
        return str(raw)[:16]


def _category_tone(category: str) -> tuple[str, str]:
    return {
        "action_required": ("Action required", "amber"),
        "information_only": ("Information only", "blue"),
        "uncertain": ("Uncertain", "gray"),
    }.get(category, (category.replace("_", " ").title() or "Unknown", "gray"))


def display_evidence(items: list[dict]) -> None:
    if not items:
        st.markdown('<p class="ip-empty">No quoted source evidence provided.</p>',
                    unsafe_allow_html=True)
        return
    for evidence in items:
        source = evidence.get("field", "body")
        st.markdown(
            f'<div class="ip-meta">quoted from {esc(source)}</div>'
            + quote_block(evidence.get("quote", "")),
            unsafe_allow_html=True,
        )


# --------------------------------------------------------------------------
# Analysis actions
# --------------------------------------------------------------------------


def _analyze(message_id: str) -> tuple[bool, str]:
    """Analyze one email and remember the result. Returns (ok, message)."""
    try:
        response = api_request(
            "POST", "/analyze", payload={"message_id": message_id}, timeout=120.0
        )
    except APIError as exc:
        return False, f"{exc.code}: {exc}"
    st.session_state.setdefault("analyses", {})[message_id] = response
    # A fresh analysis invalidates any proposal derived from the old one.
    if st.session_state.get("message_id") == message_id:
        st.session_state.pop("plan_response", None)
        st.session_state.pop("submitted_payload", None)
        st.session_state.pop("execution_result", None)
    return True, ""


def _analyze_many(message_ids: list[str]) -> None:
    total = len(message_ids)
    progress = st.progress(0.0, text=f"Analyzing 0/{total}...")
    failures: list[str] = []
    for index, message_id in enumerate(message_ids, start=1):
        progress.progress((index - 1) / total, text=f"Analyzing {index}/{total}...")
        ok, detail = _analyze(message_id)
        if not ok:
            failures.append(detail)
    progress.progress(1.0, text=f"Analyzed {total - len(failures)}/{total}")
    if failures:
        st.error("Some emails could not be analyzed: " + "; ".join(sorted(set(failures))))
    st.rerun()


# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------


def show_connection() -> None:
    st.markdown("### Connect Gmail")
    st.caption("Authorize a mailbox before working with real email.")
    status = _auth_status()
    if status.get("failed"):
        st.error(f"Cannot reach the backend: {status.get('error', 'unknown error')}")
        if st.button("Try again"):
            forget_status_cache()
            st.rerun()
        return

    if status.get("connected"):
        account = status.get("email") or "authorized account"
        st.markdown(
            f"{badge('connected', 'green')} &nbsp; **{esc(account)}**",
            unsafe_allow_html=True,
        )
        st.caption(
            "A saved credential is not proof that the token is still valid; "
            "reading the inbox verifies access."
        )
        left, right = st.columns([1, 1])
        if left.button("Open inbox", type="primary", use_container_width=True):
            go("Inbox")
        if right.button("Disconnect stored credential", use_container_width=True):
            try:
                api_request("POST", "/auth/disconnect")
                clear_email_results()
                forget_status_cache()
                st.rerun()
            except APIError as exc:
                error_message(exc)
    elif not status.get("client_configured"):
        st.warning("Google OAuth is not configured in the FastAPI backend.")
        st.code("GOOGLE_CLIENT_ID=...\nGOOGLE_CLIENT_SECRET=...\nGOOGLE_REDIRECT_URI=...")
    else:
        st.info("Gmail is not connected.")
        st.link_button("Authorize Gmail", f"{API_BASE}/auth/google", type="primary")
        st.caption(
            "Google shows an 'unverified app' warning for restricted Gmail scopes. "
            "Choose Advanced, then continue — the app only requests read-only access "
            "plus draft creation."
        )
        if st.button("I have authorized — check again"):
            forget_status_cache()
            st.rerun()
    st.divider()
    st.caption(
        "Security: email is read-only in the UI. Creating a draft requires a "
        "separate explicit approval and never sends."
    )


# --------------------------------------------------------------------------
# Inbox — the list
# --------------------------------------------------------------------------


def _fetch_inbox(query: str, limit: int) -> bool:
    try:
        with st.spinner("Reading Gmail..."):
            result = api_request(
                "GET", "/emails",
                params={"max_results": int(limit), **({"q": query} if query.strip() else {})},
                timeout=45.0,
            )
    except APIError as exc:
        error_message(exc)
        return False
    st.session_state.email_list = result.get("emails", [])
    # A new list means the previous selection no longer refers to these rows.
    for key in [k for k in st.session_state if str(k).startswith("sel_")]:
        st.session_state.pop(key, None)
    return True


def _apply_pending_selection(emails: list[dict]) -> None:
    """Apply 'select all' / 'clear' before the checkboxes are built.

    Streamlit refuses to change a widget's key after that widget exists in the
    current run, so the intent is stored in a flag and applied here on the next
    pass, ahead of rendering.
    """
    if st.session_state.pop("_clear_selection", False):
        for item in emails:
            st.session_state.pop(f"sel_{item.get('id')}", None)
    if st.session_state.pop("_select_all", False):
        for item in emails:
            st.session_state[f"sel_{item.get('id')}"] = True


def _email_row(item: dict, analysis: dict | None) -> None:
    message_id = item.get("id") or ""
    subject = item.get("subject") or "(no subject)"
    dot = '<span class="ip-dot">●</span>' if item.get("unread") else ""
    snippet = (item.get("snippet") or "").strip() or "No preview available."

    with st.container(border=True):
        select_col, body_col, action_col = st.columns(
            [0.35, 7.0, 1.5], vertical_alignment="center"
        )
        with select_col:
            st.checkbox(
                f"Select {subject}",
                key=f"sel_{message_id}",
                label_visibility="collapsed",
            )
        with body_col:
            tags = ""
            if analysis:
                category = analysis.get("analysis", {}).get("category", "")
                category_label, tone = _category_tone(category)
                tags = " " + badge(category_label, tone)
            st.markdown(
                f'<div class="ip-subject">{dot}{esc(subject)}{tags}</div>'
                f'<div class="ip-meta">{esc(_sender(item))} &nbsp;·&nbsp; {esc(_when(item))}</div>'
                f'<div class="ip-snippet">{esc(snippet)}</div>',
                unsafe_allow_html=True,
            )
        with action_col:
            button_label = "Re-analyze" if analysis else "Analyze"
            if st.button(
                button_label, key=f"an_{message_id}",
                type="primary" if not analysis else "secondary",
                use_container_width=True,
            ):
                with st.spinner("Nemotron is reading this email..."):
                    ok, detail = _analyze(message_id)
                if ok:
                    st.rerun()
                else:
                    st.error(detail)


def _analysis_card(item: dict, response: dict) -> None:
    analysis = response.get("analysis", {})
    usage = response.get("usage") or {}
    subject = item.get("subject") or "(no subject)"
    category = analysis.get("category", "")
    category_label = _category_tone(category)[0]
    needs_action = bool(analysis.get("action_required"))
    facts = analysis.get("key_facts", []) or []
    actions = analysis.get("action_items", []) or []

    header = (
        f"{subject}  ·  {category_label}"
        + ("  ·  needs a reply" if needs_action else "")
    )
    with st.expander(header, expanded=False):
        st.markdown(
            chips(category, needs_action, len(facts), len(actions)),
            unsafe_allow_html=True,
        )
        st.markdown(
            label("Summary") + summary_block(analysis.get("summary", "")),
            unsafe_allow_html=True,
        )

        st.markdown(label("Key facts"), unsafe_allow_html=True)
        if facts:
            for fact in facts:
                st.markdown(fact_block(fact), unsafe_allow_html=True)
                for evidence in fact.get("evidence", []) or []:
                    st.markdown(quote_block(evidence.get("quote", "")), unsafe_allow_html=True)
        else:
            st.markdown('<p class="ip-empty">No key facts extracted.</p>',
                        unsafe_allow_html=True)

        st.markdown(label("Action items", "amber"), unsafe_allow_html=True)
        if actions:
            for action in actions:
                st.markdown(task_block(action), unsafe_allow_html=True)
        else:
            st.markdown('<p class="ip-empty">No actionable tasks identified.</p>',
                        unsafe_allow_html=True)

        if analysis.get("uncertainties"):
            st.markdown(label("Uncertainties"), unsafe_allow_html=True)
            for unknown in analysis["uncertainties"]:
                st.markdown(f"- {esc(unknown)}", unsafe_allow_html=True)

        st.caption(
            f"{analysis.get('model', 'unknown')} · "
            f"{usage.get('latency_ms', '?')} ms · "
            f"{usage.get('total_tokens', '?')} tokens · "
            f"{'cached' if response.get('cached') else 'fresh'}"
        )
        if st.button("Open in Analysis", key=f"plan_{item.get('id')}", type="primary"):
            st.session_state.message_id = item.get("id")
            st.session_state.pop("plan_response", None)
            go("Analysis")


def show_inbox() -> None:
    st.markdown("### Inbox")
    auth = _auth_status()
    if auth.get("failed"):
        st.error(f"Cannot reach the backend: {auth.get('error', 'unknown error')}")
        return
    if not auth.get("connected"):
        st.session_state.pop("email_list", None)
        st.warning("Connect Gmail before viewing its inbox.")
        if st.button("Go to Connect"):
            go("Connect")
        return

    with st.container(border=False):
        search_col, size_col, load_col = st.columns(
            [5, 1.4, 1.4], vertical_alignment="bottom"
        )
        query = search_col.text_input(
            "Gmail search query",
            value=st.session_state.get("inbox_query", ""),
            placeholder="newer_than:7d · is:unread · from:lecturer",
            label_visibility="collapsed",
        )
        size = size_col.selectbox(
            "How many", PAGE_SIZES, index=PAGE_SIZES.index(DEFAULT_PAGE_SIZE),
            format_func=lambda n: f"{n} emails", label_visibility="collapsed",
        )
        load = load_col.button("Load inbox", type="primary", use_container_width=True)

    st.session_state.inbox_query = query
    if load or "email_list" not in st.session_state:
        if not _fetch_inbox(query, size):
            return

    emails = st.session_state.get("email_list", [])
    if not emails:
        st.info("No emails were returned for that query.")
        return

    _apply_pending_selection(emails)
    analyses = st.session_state.get("analyses", {})
    analyzed_count = sum(1 for item in emails if item.get("id") in analyses)

    st.markdown(
        f'<div class="ip-meta">{len(emails)} emails · {analyzed_count} analyzed'
        f' · {len([i for i in emails if i.get("unread")])} unread</div>',
        unsafe_allow_html=True,
    )

    for item in emails:
        _email_row(item, analyses.get(item.get("id")))

    selected = [
        item.get("id") for item in emails if st.session_state.get(f"sel_{item.get('id')}")
    ]
    st.write("")
    batch_left, batch_mid, batch_right = st.columns([3, 1.4, 1.4])
    with batch_left:
        st.markdown(
            f'<div class="ip-meta">{len(selected)} of {len(emails)} selected</div>',
            unsafe_allow_html=True,
        )
    with batch_mid:
        if st.button("Select all", use_container_width=True):
            st.session_state["_select_all"] = True
            st.rerun()
    with batch_right:
        if st.button(
            f"Analyze selected ({len(selected)})",
            type="primary", disabled=not selected, use_container_width=True,
        ):
            _analyze_many(selected)

    if analyzed_count:
        st.divider()
        st.markdown("### Analysis results")
        st.caption(
            "One card per analyzed email. Re-running an analysis replaces its card "
            "and costs nothing while the result is cached."
        )
        for item in emails:
            response = analyses.get(item.get("id"))
            if response:
                _analysis_card(item, response)


# --------------------------------------------------------------------------
# Analysis — deep dive for one email, then propose a reply
# --------------------------------------------------------------------------


def show_analysis() -> None:
    st.markdown("### Analysis")
    message_id = st.session_state.get("message_id")
    if not message_id:
        st.info("Pick an email in the inbox first.")
        if st.button("Go to inbox"):
            go("Inbox")
        return

    if st.button("Back to inbox"):
        go("Inbox")

    emails = st.session_state.get("email_list", [])
    item = next((e for e in emails if e.get("id") == message_id), None)
    subject = (item or {}).get("subject") or "(no subject)"
    st.markdown(
        f"**{esc(subject)}**<div class='ip-meta'>{esc(_sender(item or {}))} "
        f"&nbsp;·&nbsp; {esc(_when(item or {}))}</div>",
        unsafe_allow_html=True,
    )

    cached = st.session_state.get("analyses", {}).get(message_id)
    left, right = st.columns([1.4, 1])
    if left.button(
        "Re-analyze" if cached else "Analyze this email",
        type="primary", use_container_width=True,
    ):
        with st.spinner("Nemotron is analyzing the email..."):
            ok, detail = _analyze(message_id)
        if ok:
            st.rerun()
        else:
            st.error(detail)
    if right.button("Full email text", use_container_width=True):
        st.session_state.show_body = not st.session_state.get("show_body", False)

    if st.session_state.get("show_body"):
        try:
            detail = api_request("GET", f"/emails/{message_id}", timeout=30.0)
            st.text_area(
                "Email body (untrusted source content)",
                value=detail.get("body_text") or detail.get("snippet") or "(Empty)",
                height=260, disabled=True,
            )
        except APIError as exc:
            error_message(exc)

    response = st.session_state.get("analyses", {}).get(message_id)
    if not response:
        st.info("Run the analysis to see summary, evidence, tasks and uncertainties.")
        return

    analysis = response.get("analysis", {})
    usage = response.get("usage") or {}
    st.markdown(
        chips(analysis.get("category", ""), bool(analysis.get("action_required"))),
        unsafe_allow_html=True,
    )
    st.markdown(
        label("Summary") + summary_block(analysis.get("summary", "")),
        unsafe_allow_html=True,
    )

    st.markdown(label("Key facts"), unsafe_allow_html=True)
    for fact in analysis.get("key_facts", []):
        st.markdown(fact_block(fact), unsafe_allow_html=True)
        with st.expander("Show the quoted source"):
            display_evidence(fact.get("evidence", []))
    if not analysis.get("key_facts"):
        st.markdown('<p class="ip-empty">No key facts extracted.</p>', unsafe_allow_html=True)

    st.markdown(label("Action items", "amber"), unsafe_allow_html=True)
    for action in analysis.get("action_items", []):
        st.markdown(task_block(action), unsafe_allow_html=True)
    if not analysis.get("action_items"):
        st.markdown('<p class="ip-empty">No actionable tasks identified.</p>',
                    unsafe_allow_html=True)

    if analysis.get("uncertainties"):
        st.markdown(label("Uncertainties"), unsafe_allow_html=True)
        for unknown in analysis["uncertainties"]:
            st.markdown(f"- {esc(unknown)}", unsafe_allow_html=True)
    if analysis.get("risk_summary"):
        st.warning(f"Risk assessment: {analysis['risk_summary']}")

    st.caption(
        f"{analysis.get('model', 'unknown')} · {usage.get('latency_ms', '?')} ms · "
        f"{usage.get('total_tokens', '?')} tokens · "
        f"{'cached' if response.get('cached') else 'fresh'}"
    )

    st.divider()
    if st.button("Propose a reply", type="primary"):
        try:
            with st.spinner("Deciding whether a reply is warranted..."):
                plan = api_request(
                    "POST", "/actions/plan",
                    payload={"message_id": message_id}, timeout=120.0,
                )
            st.session_state.plan_response = plan
            st.session_state.pop("selected_proposal_id", None)
            st.session_state.pop("submitted_payload", None)
            st.session_state.pop("execution_result", None)
        except APIError as exc:
            error_message(exc)

    plan = st.session_state.get("plan_response")
    if plan:
        actions = plan.get("actions", [])
        st.markdown('<div class="ip-label">Proposals</div>', unsafe_allow_html=True)
        if not actions:
            st.info(
                "The agent declined to propose a reply. It does that when the sender "
                "cannot be replied to, or when nothing in the email needs an answer."
            )
        for action in actions:
            st.markdown(
                f"**{esc(action.get('title', 'Action'))}** — {esc(action.get('why', ''))}",
                unsafe_allow_html=True,
            )
            st.caption(f"Operation: {action.get('kind', 'none')} · never sends")
        if any(x.get("kind") == "create_draft" and x.get("draft") for x in actions):
            if st.button("Review the draft", type="primary"):
                go("Action Review")


# --------------------------------------------------------------------------
# Action Review — the approval gate
# --------------------------------------------------------------------------


def show_action_review() -> None:
    st.markdown("### Review and approve")
    st.info("This creates a Gmail **draft** only. It will not send an email.")

    plan = st.session_state.get("plan_response") or {}
    actions = [
        a for a in plan.get("actions", [])
        if a.get("kind") == "create_draft" and a.get("draft") and a.get("id")
    ]
    if not actions:
        st.warning(
            "No server-generated draft proposal is available. Analyze an email and "
            "propose a reply first."
        )
        if st.button("Back to analysis"):
            go("Analysis")
        return

    by_id = {a["id"]: a for a in actions}
    selected_id = st.selectbox(
        "Suggested action",
        list(by_id.keys()),
        format_func=lambda i: by_id[i].get("title", "Draft proposal"),
    )
    if st.session_state.get("selected_proposal_id") != selected_id:
        st.session_state.selected_proposal_id = selected_id
        st.session_state.pop("submitted_payload", None)
        st.session_state.pop("execution_result", None)

    action = by_id[selected_id]
    proposal = action["draft"]
    st.caption(action.get("why", ""))
    missing = action.get("missing_info", [])
    if missing:
        st.warning("The agent could not determine: " + "; ".join(missing))
        st.caption(
            "These appear as bracketed placeholders in the draft body. Replace them "
            "before approving: the backend will not create a draft while one remains."
        )

    auth = _auth_status()
    if auth.get("failed"):
        auth = {}
    approver_email = auth.get("email") or ""
    completed = st.session_state.get("execution_result", {}).get("status") == "created"

    with st.form(f"review_{selected_id}"):
        to_value = st.text_input("To (comma-separated)", value=", ".join(proposal.get("to") or []))
        cc_value = st.text_input("CC (comma-separated)", value=", ".join(proposal.get("cc") or []))
        subject = st.text_input("Subject", value=proposal.get("subject") or "")
        body = st.text_area("Draft body", value=proposal.get("body") or "", height=250)
        approver = st.text_input("Approving account", value=approver_email)
        approved = st.checkbox(
            "I have reviewed this draft and explicitly approve creating it in Gmail."
        )
        submit = st.form_submit_button(
            "Create Gmail draft", type="primary", disabled=completed
        )

    if submit:
        to_list = [v.strip() for v in to_value.split(",") if v.strip()]
        cc_list = [v.strip() for v in cc_value.split(",") if v.strip()]
        if not approved:
            st.error("You must explicitly approve this operation.")
        elif not auth.get("connected") or not approver_email:
            st.error("A Gmail account must be connected before creating a draft.")
        elif approver != approver_email:
            st.error("The approving account must match the connected Gmail account.")
        elif not to_list or not subject.strip() or not body.strip():
            st.error("A recipient, subject and body are required.")
        else:
            draft = {
                "to": to_list,
                "cc": cc_list,
                "subject": subject.strip(),
                "body": body,
                "in_reply_to": proposal.get("in_reply_to"),
                "thread_id": proposal.get("thread_id"),
            }
            if "submitted_payload" not in st.session_state:
                st.session_state.submitted_payload = {
                    "proposal_id": selected_id,
                    "idempotency_key": uuid.uuid4().hex,
                    "draft": draft,
                    "approved_by": approver_email,
                    "approved": True,
                }
            elif st.session_state.submitted_payload["draft"] != draft:
                st.error(
                    "This proposal already has a submission attempt with different content. "
                    "To avoid duplicate writes, do not retry with changed fields. "
                    "Verify Gmail first and request a fresh proposal if necessary."
                )
                st.stop()

            try:
                with st.spinner("Creating and verifying the Gmail draft..."):
                    result = api_request(
                        "POST", "/actions/draft",
                        payload=st.session_state.submitted_payload, timeout=60.0,
                    )
                st.session_state.execution_result = result
            except APIError as exc:
                error_message(exc)

    result = st.session_state.get("execution_result")
    if result:
        if result.get("status") == "created" and result.get("verified"):
            st.success(
                f"Draft created and read back from Gmail. Draft ID: {result.get('draft_id')}"
            )
            st.caption("Open Gmail → Drafts to see it. Nothing was sent.")
        elif result.get("status") == "created":
            st.warning(
                "Gmail reported draft creation, but reading it back did not confirm "
                "the content. Check Gmail → Drafts before retrying."
            )
        else:
            st.error(result.get("message") or f"Action status: {result.get('status')}")


# --------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="InboxPilot", page_icon="📥", layout="wide")
    inject_css()

    st.session_state.setdefault("nav_page", "Connect")
    st.session_state.setdefault("analyses", {})
    requested = st.session_state.pop("requested_page", None)
    if requested in PAGES:
        st.session_state.nav_page = requested

    with st.sidebar:
        st.markdown("## InboxPilot")
        st.caption("From email information to real-world action.")
        page = st.radio("Navigation", PAGES, key="nav_page", label_visibility="collapsed")
        st.divider()
        health = _health_snapshot()
        if health["ok"]:
            data = health["data"]
            st.markdown(badge("backend online", "green"), unsafe_allow_html=True)
            st.caption(f"env: {data.get('env', '?')} · config: {data.get('status', '?')}")
            if data.get("status") != "ok":
                st.warning("Some integrations need configuration.")
        else:
            st.markdown(badge("backend offline", "red"), unsafe_allow_html=True)
            st.caption(health["error"])
        if st.button("Recheck", use_container_width=True):
            forget_status_cache()
            st.rerun()
        st.divider()
        analyses = st.session_state.get("analyses", {})
        st.caption(f"analyzed this session: {len(analyses)}")

    if page == "Connect":
        show_connection()
    elif page == "Inbox":
        show_inbox()
    elif page == "Analysis":
        show_analysis()
    elif page == "Action Review":
        show_action_review()


if __name__ == "__main__":
    main()
