"""Gmail access layer: OAuth (A3) and reading (A5).

Rules that must survive implementation:
- Scopes stay minimal: gmail.readonly + gmail.compose. Never gmail.modify.
- The refresh token is server-side only (see token_store).
- `create_draft` is the ONLY write operation. There is no send/delete/archive.
- Creating a draft must be idempotent: the caller passes an idempotency key and
  the result is stored, so a retry cannot produce a second draft.
- Email content is untrusted data. Nothing here interprets it as instructions.
"""

from __future__ import annotations

import base64
import binascii
import logging
import secrets
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterator

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.config import get_settings
from backend.errors import AppError, AuthError, ConfigError, NotFound, UpstreamError
from backend.schemas import EmailDetail, EmailSummaryItem
from backend.services import token_store

log = logging.getLogger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

# In-flight OAuth attempts: state -> {"issued_at": float, "code_verifier": str}.
#
# The PKCE verifier MUST be stored with the state. google-auth-oauthlib 1.5
# defaults to `autogenerate_code_verifier=True`, so every Flow instance gets a
# fresh verifier. The authorization URL is built by one Flow and the token
# exchange by another, so without this the exchange sends a verifier that does
# not match the code_challenge and Google answers `invalid_grant` — every time,
# not intermittently.
#
# In-memory is fine for the single-process local/dev flow. A deployment that
# runs more than one worker would need this in shared storage; the judge-facing
# demo does not use this flow at all (it uses DEMO_REFRESH_TOKEN).
_PENDING_STATES: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600


# --------------------------------------------------------------------------
# OAuth (task A3)
# --------------------------------------------------------------------------


def is_configured() -> bool:
    return get_settings().is_google_configured


def _client_config() -> dict[str, Any]:
    settings = get_settings()
    if not settings.is_google_configured:
        raise ConfigError(
            "Google OAuth client is not configured",
            details={"env_vars": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]},
        )
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
        }
    }


def _build_flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    settings = get_settings()
    return Flow.from_client_config(
        _client_config(),
        scopes=settings.google_scope_list,
        redirect_uri=settings.google_redirect_uri,
        state=state,
        # Explicit, not inherited: the verifier is persisted by
        # get_authorization_url and restored by exchange_code.
        autogenerate_code_verifier=True,
    )


def get_authorization_url() -> str:
    """Consent URL. `access_type=offline` + `prompt=consent` guarantee a refresh token."""
    state = secrets.token_urlsafe(24)

    # Drop expired states so the dict cannot grow without bound.
    now = time.time()
    for stale in [
        s for s, rec in _PENDING_STATES.items()
        if now - rec["issued_at"] > _STATE_TTL_SECONDS
    ]:
        _PENDING_STATES.pop(stale, None)

    flow = _build_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    # Keep the verifier that produced this URL's code_challenge.
    _PENDING_STATES[state] = {"issued_at": now, "code_verifier": flow.code_verifier}
    return url


def _consume_state(state: str | None) -> dict[str, Any]:
    if not state:
        raise AuthError("Missing OAuth state. Start the flow again from /auth/google.")
    record = _PENDING_STATES.pop(state, None)
    if record is None:
        raise AuthError("Unknown OAuth state. Start the flow again from /auth/google.")
    if time.time() - record["issued_at"] > _STATE_TTL_SECONDS:
        raise AuthError("OAuth state expired. Start the flow again from /auth/google.")
    return record


def _oauth_error_text(exc: Exception) -> str:
    """Secret-free description of an OAuth failure, safe to log and to return."""
    parts = [
        str(getattr(exc, "error", "") or ""),
        str(getattr(exc, "description", "") or ""),
        str(exc),
    ]
    return " | ".join(dict.fromkeys(p for p in parts if p))[:300]


def exchange_code(code: str, state: str | None) -> dict:
    """Exchange the callback code for a refresh token and store it."""
    record = _consume_state(state)
    flow = _build_flow(state=state)
    # Reuse the verifier behind this URL's code_challenge (PKCE).
    flow.code_verifier = record.get("code_verifier")
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 - oauthlib raises many types
        detail = _oauth_error_text(exc)
        log.warning(
            "gmail.oauth_exchange_failed type=%s detail=%s",
            type(exc).__name__,
            detail,
        )
        raise AuthError(
            "Google rejected the authorization code",
            details={"reason": type(exc).__name__, "google": detail},
        ) from exc

    credentials = flow.credentials
    refresh_token = credentials.refresh_token
    if not refresh_token:
        raise AuthError(
            "Google did not return a refresh token. Remove the app from your Google "
            "account permissions and authorize again."
        )

    email = _profile_email(credentials)
    token_store.save(
        refresh_token=refresh_token,
        email=email,
        scopes=list(credentials.scopes or get_settings().google_scope_list),
    )
    log.info("gmail.oauth_connected email=%s", email)
    return {"connected": True, "email": email}


def connection_status() -> dict:
    """Booleans + the account address. Never the token."""
    settings = get_settings()
    if not settings.is_google_configured:
        return {
            "client_configured": False,
            "connected": False,
            "email": None,
            "demo_account_configured": settings.is_demo_account_configured,
        }
    state = token_store.status()
    return {
        "client_configured": True,
        "connected": state["connected"],
        "email": state["email"],
        "source": state["source"],
        "scopes": state["scopes"],
        "demo_account_configured": settings.is_demo_account_configured,
    }


def disconnect() -> dict:
    """Forget the local token. The user can revoke access from their Google account."""
    token_store.clear()
    return {"connected": False}


# --------------------------------------------------------------------------
# Credentials and service
# --------------------------------------------------------------------------


def _credentials() -> Credentials:
    settings = get_settings()
    data = token_store.load()
    if not data:
        raise AuthError(
            "Gmail is not connected",
            details={"next_step": "Open /auth/google to authorize an account"},
        )

    credentials = Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=settings.google_scope_list,
    )
    try:
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:  # noqa: BLE001 - RefreshError and transport errors
        raise AuthError(
            "The stored Gmail credential is no longer valid. Re-authorize the account.",
            details={"reason": type(exc).__name__},
        ) from exc
    return credentials


def _service():
    return build("gmail", "v1", credentials=_credentials(), cache_discovery=False)


def _profile_email(credentials: Credentials) -> str | None:
    try:
        profile = (
            build("gmail", "v1", credentials=credentials, cache_discovery=False)
            .users()
            .getProfile(userId="me")
            .execute()
        )
        return profile.get("emailAddress")
    except Exception as exc:  # noqa: BLE001 - not fatal, only used for display
        log.warning("gmail.profile_failed type=%s", type(exc).__name__)
        return None


def _map_http_error(exc: HttpError, context: str) -> Exception:
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 400:
        # Gmail answers 400 for a malformed message/thread id, which for a read
        # means "no such email" — not "the server is broken".
        if context in ("messages.get", "threads.get"):
            return NotFound("The requested email does not exist.")
        return UpstreamError(
            "Gmail rejected the request parameters.",
            details={"status": 400, "context": context},
        )
    if status == 401:
        return AuthError("Gmail rejected the credential. Re-authorize the account.")
    if status == 404:
        return NotFound("The requested email or thread does not exist.")
    if status == 403:
        return UpstreamError(
            "Gmail refused the request. The account may lack the required scope, "
            "or the API quota is exhausted.",
            details={"status": 403, "context": context},
        )
    if status == 429:
        return UpstreamError(
            "Gmail rate limit reached. Try again in a moment.",
            details={"status": 429, "context": context},
        )
    return UpstreamError(
        "Gmail request failed",
        details={"status": status, "context": context},
    )


# --------------------------------------------------------------------------
# MIME parsing (pure functions, covered by tests)
# --------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.chunks.append(data)


def html_to_text(html: str) -> str:
    """Small, dependency-free HTML to text conversion for email bodies."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not break analysis
        return ""
    text = "".join(parser.chunks)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def decode_body(data: str | None) -> str:
    """Gmail returns base64url without padding."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return ""


def iter_parts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield payload
    for part in payload.get("parts", []) or []:
        yield from iter_parts(part)


def extract_bodies(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return (text, html) from a Gmail message payload."""
    text_chunks: list[str] = []
    html_chunks: list[str] = []
    for part in iter_parts(payload):
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        if mime == "text/plain":
            decoded = decode_body(body.get("data"))
            if decoded:
                text_chunks.append(decoded)
        elif mime == "text/html":
            decoded = decode_body(body.get("data"))
            if decoded:
                html_chunks.append(decoded)

    html = "\n".join(html_chunks) if html_chunks else None
    text = "\n\n".join(c.strip() for c in text_chunks if c.strip())
    if not text and html:
        text = html_to_text(html)
    return text.strip(), html


def header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = (payload.get("headers") or [])
    return {h.get("name", "").lower(): h.get("value", "") for h in headers if h.get("name")}


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _split_addresses(value: str) -> list[str]:
    return [a.strip() for a in value.split(",") if a.strip()]


def to_summary(message: dict[str, Any]) -> EmailSummaryItem:
    payload = message.get("payload", {}) or {}
    headers = header_map(payload)
    labels = message.get("labelIds", []) or []
    return EmailSummaryItem(
        id=message.get("id", ""),
        thread_id=message.get("threadId"),
        subject=headers.get("subject", ""),
        sender=headers.get("from", ""),
        to=_split_addresses(headers.get("to", "")),
        date=parse_date(headers.get("date")),
        snippet=message.get("snippet", "") or "",
        unread="UNREAD" in labels,
        labels=labels,
    )


def to_detail(message: dict[str, Any]) -> EmailDetail:
    summary = to_summary(message)
    payload = message.get("payload", {}) or {}
    text, html = extract_bodies(payload)
    return EmailDetail(
        **summary.model_dump(),
        body_text=text,
        body_html=html,
        headers=header_map(payload),
    )


# --------------------------------------------------------------------------
# Reading (task A5)
# --------------------------------------------------------------------------


def list_emails(*, max_results: int | None = None, query: str | None = None) -> list[EmailSummaryItem]:
    settings = get_settings()
    # Explicit None check: `max_results or 10` would turn a request for 0 into 10.
    requested = 10 if max_results is None else max_results
    limit = max(1, min(requested, settings.max_emails_per_request))
    service = _service()
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", maxResults=limit, q=query)
            .execute()
        )
    except HttpError as exc:
        raise _map_http_error(exc, "messages.list") from exc

    ids = [m["id"] for m in response.get("messages", []) or []]
    emails: list[EmailSummaryItem] = []
    for message_id in ids:
        try:
            message = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["Subject", "From", "To", "Date"],
                )
                .execute()
            )
        except HttpError as exc:
            # One unreadable message must not take down the whole inbox.
            log.warning("gmail.message_unavailable id=%s status=%s", message_id,
                        getattr(getattr(exc, "resp", None), "status", "?"))
            continue
        emails.append(to_summary(message))
    return emails


def get_email(message_id: str) -> EmailDetail:
    service = _service()
    try:
        message = (
            service.users().messages().get(userId="me", id=message_id, format="full").execute()
        )
    except HttpError as exc:
        raise _map_http_error(exc, "messages.get") from exc
    return to_detail(message)


def get_thread(thread_id: str) -> list[EmailDetail]:
    service = _service()
    try:
        thread = (
            service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        )
    except HttpError as exc:
        raise _map_http_error(exc, "threads.get") from exc
    return [to_detail(m) for m in thread.get("messages", []) or []]


# --------------------------------------------------------------------------
# Writing (task A11, Day 4)
# --------------------------------------------------------------------------


def _validate_recipients(addresses: list[str], field: str) -> list[str]:
    """Reject anything that is not a plain address before it reaches Gmail.

    Deliberately strict: a header-injection attempt (`a@b.com\\nBcc: ...`) and a
    typo look identical to Gmail, and both would produce a draft nobody asked
    for. Anything with a control character, a newline, or no `@` is refused.
    """
    cleaned: list[str] = []
    for raw in addresses:
        value = (raw or "").strip()
        if not value:
            continue
        if any(ch in value for ch in "\r\n\t") or value.count("@") != 1:
            raise AppError(
                f"A {field} address is not valid",
                code="invalid_recipient",
                status_code=422,
                details={"field": field},
            )
        _, address = parseaddr(value)
        if not address or "@" not in address or address.count("@") != 1:
            raise AppError(
                f"A {field} address is not valid",
                code="invalid_recipient",
                status_code=422,
                details={"field": field},
            )
        cleaned.append(value)
    if not cleaned:
        raise AppError(
            f"At least one {field} address is required",
            code="invalid_recipient",
            status_code=422,
            details={"field": field},
        )
    return cleaned


def build_mime(
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> bytes:
    """Serialise a draft to RFC 5322 bytes.

    `EmailMessage` handles the two encoding traps that hand-rolled MIME gets
    wrong: a non-ASCII subject becomes an RFC 2047 encoded word, and a non-ASCII
    body is declared UTF-8 rather than silently sent as latin-1. Both matter
    here because the demo mailbox is Malaysian and the emails contain accents
    and Malay text.
    """
    message = EmailMessage()
    message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    # A draft needs its own Message-ID so a later reply can reference it.
    message["Message-ID"] = make_msgid(domain="inboxpilot.local")
    if in_reply_to:
        # These two headers are what make Gmail show the draft inside the
        # existing conversation instead of as a brand new thread.
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to
    message.set_content(body, subtype="plain", charset="utf-8", cte="base64")
    return message.as_bytes()


def create_draft(
    *,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    """Create a draft in the connected mailbox. Returns the Gmail draft id.

    This is the only function in the codebase that writes to Gmail. It cannot
    send: `drafts.create` has no send semantics, and no `messages.send` call
    exists anywhere in the project.
    """
    recipients = _validate_recipients(to, "recipient")
    cc_recipients = _validate_recipients(cc, "cc") if cc else []
    if not (subject or "").strip():
        raise AppError(
            "A draft needs a subject",
            code="invalid_draft",
            status_code=422,
            details={"field": "subject"},
        )
    if not (body or "").strip():
        raise AppError(
            "A draft needs a body",
            code="invalid_draft",
            status_code=422,
            details={"field": "body"},
        )

    raw = base64.urlsafe_b64encode(
        build_mime(
            to=recipients,
            cc=cc_recipients,
            subject=subject.strip(),
            body=body,
            in_reply_to=in_reply_to,
            references=references,
        )
    ).decode("ascii")

    request: dict[str, Any] = {"message": {"raw": raw}}
    if thread_id:
        request["message"]["threadId"] = thread_id

    service = _service()
    try:
        draft = service.users().drafts().create(userId="me", body=request).execute()
    except HttpError as exc:
        raise _map_http_error(exc, "drafts.create") from exc

    draft_id = draft.get("id")
    if not draft_id:
        raise UpstreamError("Gmail accepted the draft but returned no draft id")
    log.info("gmail.draft_created draft_id=%s thread_id=%s", draft_id, thread_id or "-")
    return draft_id


def get_draft(draft_id: str) -> dict[str, Any]:
    """Read a draft back. Used to prove the write actually happened.

    Returns a flat, comparable dict rather than Gmail's payload tree, so the
    verification step compares values instead of re-walking the MIME structure.
    """
    service = _service()
    try:
        draft = (
            service.users()
            .drafts()
            .get(userId="me", id=draft_id, format="full")
            .execute()
        )
    except HttpError as exc:
        raise _map_http_error(exc, "drafts.get") from exc

    message = draft.get("message") or {}
    payload = message.get("payload") or {}
    headers = header_map(payload)
    body_text, _ = extract_bodies(payload)
    return {
        "draft_id": draft.get("id") or draft_id,
        "message_id": message.get("id"),
        "thread_id": message.get("threadId") or draft.get("message", {}).get("threadId"),
        "to": _split_addresses(headers.get("to", "")),
        "cc": _split_addresses(headers.get("cc", "")),
        "subject": headers.get("subject", ""),
        "body_text": body_text,
        "rfc_message_id": headers.get("message-id", ""),
    }
