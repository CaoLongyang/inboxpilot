"""Gmail access layer (FR-01 / FR-02 / FR-08).

Status: skeleton. The real implementation lands in A3 (OAuth), A5 (read) and
A11 (create draft). Until then every method raises NotImplementedYet so that
nothing pretends to work.

Rules that must survive implementation:
- Scopes stay minimal: gmail.readonly + gmail.compose. Never gmail.modify.
- The OAuth refresh token is server-side only. It is never sent to the frontend
  and never written to logs or the repository.
- `create_draft` is the ONLY write operation. There is no send/delete/archive.
- Creating a draft must be idempotent: the caller passes an idempotency key and
  the result is stored, so a retry cannot produce a second draft.
"""

from __future__ import annotations

import logging

from backend.config import get_settings
from backend.errors import ConfigError, NotImplementedYet

log = logging.getLogger(__name__)


def is_configured() -> bool:
    settings = get_settings()
    return settings.is_google_configured


def get_authorization_url() -> str:
    """Step 1 of the OAuth flow (A3)."""
    raise NotImplementedYet("Gmail OAuth is not implemented yet (task A3)")


def exchange_code(code: str) -> None:
    """Step 2: exchange the callback code for credentials and store the token (A3)."""
    raise NotImplementedYet("Gmail OAuth callback is not implemented yet (task A3)")


def connection_status() -> dict:
    """Whether a usable credential exists (A3)."""
    settings = get_settings()
    if not settings.is_google_configured:
        raise ConfigError(
            "Google OAuth client is not configured",
            details={"env_vars": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]},
        )
    return {
        "client_configured": True,
        "connected": False,
        "demo_account_configured": settings.is_demo_account_configured,
    }


def list_emails(*, max_results: int | None = None, query: str | None = None) -> list[dict]:
    """A5: messages.list + minimal metadata for the inbox view."""
    raise NotImplementedYet("Email listing is not implemented yet (task A5)")


def get_email(message_id: str) -> dict:
    """A5: messages.get, text and HTML body extracted."""
    raise NotImplementedYet("Email detail is not implemented yet (task A5)")


def get_thread(thread_id: str) -> list[dict]:
    """A5: threads.get, ordered messages."""
    raise NotImplementedYet("Thread reading is not implemented yet (task A5)")


def create_draft(
    *,
    to: list[str],
    subject: str,
    body: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> str:
    """A11: build MIME, create the draft, return the Gmail draft id."""
    raise NotImplementedYet("Draft creation is not implemented yet (task A11)")
