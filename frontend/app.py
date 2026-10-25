"""InboxPilot Streamlit frontend.

Owner: B — Product Experience / Agent UX.
Uses the existing FastAPI contract in backend/schemas.py; no direct Gmail
or Nebius calls, and no OAuth or model secrets in the browser.

Start:
    python -m streamlit run frontend/app.py
Backend:
    python -m uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import streamlit as st


API_BASE = os.getenv("INBOXPILOT_API_URL", "http://localhost:8000").rstrip("/")
PAGES = ["Connect", "Inbox", "Analysis", "Action Review"]


class APIError(Exception):
    def __init__(self, message: str, code: str = "api_error"):
        super().__init__(message)
        self.code = code


def api_request(
    method: str, path: str, *, params: dict | None = None,
    payload: dict | None = None, timeout: float = 15.0,
) -> dict[str, Any]:
    """Call the backend; respect its documented error envelope."""
    try:
        response = httpx.request(
            method, f"{API_BASE}{path}",
            params=params, json=payload, timeout=timeout,
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
    if exc.code == "not_implemented":
        st.warning(f"This backend endpoint is not implemented yet: {exc}")
    elif exc.code == "not_authenticated":
        st.warning(f"Gmail authorization required: {exc}")
    else:
        st.error(f"{exc.code}: {exc}")


def go(page: str) -> None:
    st.session_state.requested_page = page
    st.rerun()


def clear_email_results() -> None:
    for name in (
        "analysis_response", "plan_response", "selected_proposal_id",
        "submitted_payload", "execution_result",
    ):
        st.session_state.pop(name, None)


def select_email(message_id: str) -> None:
    if st.session_state.get("message_id") != message_id:
        st.session_state.message_id = message_id
        clear_email_results()


def display_evidence(items: list[dict]) -> None:
    if not items:
        st.caption("No quoted source evidence provided.")
        return
    for evidence in items:
        source = evidence.get("field", "body")
        quote = evidence.get("quote", "")
        st.caption(f"Source: {source} · Message: {evidence.get('message_id', '?')}")
        st.code(quote, language=None, wrap_lines=True)


def show_connection() -> None:
    st.header("Connect Gmail")
    st.write("Authorize a mailbox before working with real email.")
    try:
        status = api_request("GET", "/auth/status", timeout=5.0)
    except APIError as exc:
        error_message(exc)
        return

    if status.get("connected"):
        st.success(f"Stored Gmail connection: {status.get('email') or 'authorized account'}")
        st.caption("A saved credential is not proof that the token is still valid; reading the inbox verifies access.")
        if st.button("Open inbox", type="primary"):
            go("Inbox")
        if st.button("Disconnect stored credential"):
            try:
                api_request("POST", "/auth/disconnect")
                clear_email_results()
                st.session_state.pop("email_list", None)
                st.session_state.pop("message_id", None)
                st.rerun()
            except APIError as exc:
                error_message(exc)
    elif not status.get("client_configured"):
        st.warning("Google OAuth is not configured in the FastAPI backend.")
        st.code("GOOGLE_CLIENT_ID=...\nGOOGLE_CLIENT_SECRET=...\nGOOGLE_REDIRECT_URI=...")
    else:
        st.info("Gmail is not connected.")
        st.link_button("Authorize Gmail", f"{API_BASE}/auth/google", type="primary")
        st.caption("After completing Google authorization, return here and refresh the connection status.")
        if st.button("Refresh connection status"):
            st.rerun()
    st.divider()
    st.caption("Security: email is read-only in the UI. Creating a draft requires a separate explicit approval.")


def show_inbox() -> None:
    st.header("Inbox")
    try:
        auth = api_request("GET", "/auth/status", timeout=5.0)
    except APIError as exc:
        error_message(exc)
        return
    if not auth.get("connected"):
        # Never display cached subjects after a mailbox is disconnected.
        st.session_state.pop("email_list", None)
        st.warning("Connect Gmail before viewing its inbox.")
        if st.button("Go to Connect"):
            go("Connect")
        return
    with st.form("mail_search"):
        left, right = st.columns([3, 1])
        with left:
            query = st.text_input("Gmail search query", placeholder="e.g. newer_than:7d")
        with right:
            count = st.number_input("Maximum emails", min_value=1, max_value=20, value=10)
        search = st.form_submit_button("Load emails", type="primary")

    if search or "email_list" not in st.session_state:
        try:
            with st.spinner("Reading Gmail..."):
                result = api_request(
                    "GET", "/emails", params={
                        "max_results": int(count),
                        **({"q": query} if query.strip() else {}),
                    }, timeout=30.0,
                )
            st.session_state.email_list = result.get("emails", [])
        except APIError as exc:
            error_message(exc)
            return

    emails = st.session_state.get("email_list", [])
    if not emails:
        st.info("No emails were returned.")
        return

    st.caption(f"{len(emails)} email(s) loaded.")
    options = [e.get("id", "") for e in emails if e.get("id")]
    if not options:
        st.warning("The backend did not return usable message IDs.")
        return

    def label(message_id: str) -> str:
        item = next(x for x in emails if x.get("id") == message_id)
        subject = item.get("subject") or "(No subject)"
        sender = item.get("from", item.get("sender", "Unknown"))
        return f"{'● ' if item.get('unread') else ''}{subject} — {sender}"

    current = st.session_state.get("message_id")
    chosen = st.selectbox(
        "Select an email", options,
        index=options.index(current) if current in options else 0,
        format_func=label,
    )
    select_email(chosen)

    try:
        detail = api_request("GET", f"/emails/{chosen}", timeout=30.0)
    except APIError as exc:
        error_message(exc)
        return

    st.subheader(detail.get("subject") or "(No subject)")
    st.caption(
        f"From: {detail.get('from', detail.get('sender', 'Unknown'))}   "
        f"| Date: {detail.get('date') or 'Unknown'}"
    )
    st.text_area(
        "Email body (untrusted source content)",
        value=detail.get("body_text") or detail.get("snippet") or "(Empty)",
        height=260, disabled=True,
    )
    if st.button("Analyze with Nemotron", type="primary"):
        go("Analysis")


def show_analysis() -> None:
    st.header("AI Analysis")
    message_id = st.session_state.get("message_id")
    if not message_id:
        st.info("Select an email in Inbox first.")
        if st.button("Go to inbox"):
            go("Inbox")
        return

    st.caption(f"Selected Gmail message: {message_id}")
    if st.button("Analyze selected email", type="primary"):
        try:
            with st.spinner("Nemotron is analyzing the email..."):
                result = api_request(
                    "POST", "/analyze", payload={"message_id": message_id},
                    timeout=90.0,
                )
            st.session_state.analysis_response = result
            st.session_state.pop("plan_response", None)
        except APIError as exc:
            error_message(exc)

    response = st.session_state.get("analysis_response")
    if not response or response.get("analysis", {}).get("message_id") != message_id:
        st.info("Run analysis to see summary, evidence, tasks and uncertainties.")
        return

    analysis = response["analysis"]
    st.subheader("Summary")
    st.write(analysis.get("summary", ""))
    col_a, col_b = st.columns(2)
    col_a.metric("Category", analysis.get("category", "unknown").replace("_", " ").title())
    col_b.metric("Action required", "Yes" if analysis.get("action_required") else "No")
    usage = response.get("usage") or {}
    st.caption(
        f"Model: {analysis.get('model', 'unknown')} · "
        f"Latency: {usage.get('latency_ms', 'not reported')} ms · "
        f"Cached: {response.get('cached', False)}"
    )

    st.subheader("Key facts")
    for fact in analysis.get("key_facts", []):
        with st.expander(f"[{fact.get('kind', 'unknown').upper()}] {fact.get('text', '')}"):
            display_evidence(fact.get("evidence", []))
    if not analysis.get("key_facts"):
        st.caption("No key facts extracted.")

    st.subheader("Action items")
    for item in analysis.get("action_items", []):
        st.markdown(f"**{item.get('title', 'Untitled action')}**")
        st.write(item.get("why", ""))
        deadline = item.get("deadline") or {}
        if deadline:
            st.caption(
                f"Deadline: {deadline.get('date') or deadline.get('text') or 'Unknown'} "
                f"({deadline.get('kind', 'unknown')})"
            )
        if item.get("risk"):
            st.warning(item["risk"])
        if item.get("needs_user_input"):
            st.info("Needs user input: " + "; ".join(item["needs_user_input"]))
        with st.expander("View supporting email evidence"):
            display_evidence(item.get("evidence", []))
    if not analysis.get("action_items"):
        st.caption("No actionable tasks identified.")

    if analysis.get("uncertainties"):
        st.subheader("Unknowns and uncertainties")
        for item in analysis["uncertainties"]:
            st.write(f"• {item}")
    if analysis.get("risk_summary"):
        st.warning(f"Risk assessment: {analysis['risk_summary']}")

    st.divider()
    if st.button("Generate proposed actions"):
        try:
            with st.spinner("Preparing action proposal..."):
                plan = api_request(
                    "POST", "/actions/plan",
                    payload={"message_id": message_id}, timeout=90.0,
                )
            st.session_state.plan_response = plan
            st.session_state.pop("selected_proposal_id", None)
            st.session_state.pop("submitted_payload", None)
            st.session_state.pop("execution_result", None)
        except APIError as exc:
            error_message(exc)

    plan = st.session_state.get("plan_response")
    if plan:
        st.subheader("Action proposals")
        actions = plan.get("actions", [])
        if not actions:
            st.info("No actions proposed for this email.")
        for action in actions:
            st.write(f"**{action.get('title', 'Action')}** — {action.get('why', '')}")
            st.caption(f"Operation: {action.get('kind', 'none')}")
        if any(x.get("kind") == "create_draft" and x.get("draft") for x in actions):
            if st.button("Review proposed draft", type="primary"):
                go("Action Review")


def show_action_review() -> None:
    st.header("Review and Approve")
    st.info("This operation creates a Gmail DRAFT only. It will NOT send an email.")

    plan = st.session_state.get("plan_response") or {}
    actions = [
        a for a in plan.get("actions", [])
        if a.get("kind") == "create_draft" and a.get("draft") and a.get("id")
    ]
    if not actions:
        st.warning("No server-generated draft proposal is available. Analyze an email and generate actions first.")
        if st.button("Back to Analysis"):
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
    st.write(action.get("why", ""))
    missing = action.get("missing_info", [])
    if missing:
        st.warning("Backend marked missing information: " + "; ".join(missing))
        st.caption("Ask the agent to regenerate the proposal after the missing information is resolved.")

    try:
        auth = api_request("GET", "/auth/status", timeout=5.0)
    except APIError:
        auth = {}
    approver_email = auth.get("email") or ""
    completed = st.session_state.get("execution_result", {}).get("status") == "created"

    with st.form(f"review_{selected_id}"):
        to_value = st.text_input("To (comma-separated)", value=", ".join(proposal.get("to") or []))
        cc_value = st.text_input("CC (comma-separated)", value=", ".join(proposal.get("cc") or []))
        subject = st.text_input("Subject", value=proposal.get("subject") or "")
        body = st.text_area("Draft body", value=proposal.get("body") or "", height=250)
        approver = st.text_input("Approving account", value=approver_email)
        approved = st.checkbox("I have reviewed this draft and explicitly approve creating it in Gmail.")
        submit = st.form_submit_button("Create Gmail draft", type="primary", disabled=completed)

    if submit:
        to_list = [v.strip() for v in to_value.split(",") if v.strip()]
        cc_list = [v.strip() for v in cc_value.split(",") if v.strip()]
        if not approved:
            st.error("You must explicitly approve this operation.")
        elif not auth.get("connected") or not approver_email:
            st.error("A Gmail account must be connected before creating a draft.")
        elif approver != approver_email:
            st.error("The approving account must match the connected Gmail account.")
        elif missing:
            st.error("The proposal still has unresolved required information.")
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
                with st.spinner("Creating and verifying Gmail draft..."):
                    result = api_request(
                        "POST", "/actions/draft",
                        payload=st.session_state.submitted_payload, timeout=45.0,
                    )
                st.session_state.execution_result = result
            except APIError as exc:
                error_message(exc)

    result = st.session_state.get("execution_result")
    if result:
        if result.get("status") == "created" and result.get("verified"):
            st.success(f"Draft created and verified. Gmail draft ID: {result.get('draft_id')}")
        elif result.get("status") == "created":
            st.warning("Gmail reported draft creation, but verification is not confirmed.")
        else:
            st.error(result.get("message") or f"Action status: {result.get('status')}")


def main() -> None:
    st.set_page_config(page_title="InboxPilot", page_icon="📥", layout="wide")
    st.session_state.setdefault("nav_page", "Connect")
    requested = st.session_state.pop("requested_page", None)
    if requested in PAGES:
        st.session_state.nav_page = requested

    st.sidebar.title("InboxPilot")
    st.sidebar.caption("From email information to real-world action.")
    page = st.sidebar.radio("Navigation", PAGES, key="nav_page")
    st.sidebar.divider()
    st.sidebar.caption("Backend")
    try:
        health = api_request("GET", "/health", timeout=4.0)
        st.sidebar.success("FastAPI reachable")
        st.sidebar.caption(
            f"Environment: {health.get('env', '?')} · "
            f"Configuration: {health.get('status', '?')}"
        )
        if health.get("status") != "ok":
            st.sidebar.warning("Backend integrations need configuration.")
    except APIError as exc:
        st.sidebar.error("FastAPI unavailable")
        st.sidebar.caption(str(exc))

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
