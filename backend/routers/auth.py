from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import RedirectResponse

from backend.services import gmail_client

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google")
async def start_google_oauth(format: str = Query("redirect", pattern="^(redirect|json)$")):
    """Start the Gmail OAuth flow (FR-01, task A3).

    Default: 302 to Google's consent screen, which is what a browser should hit.
    `?format=json` returns the URL instead, for the Streamlit frontend.
    """
    url = gmail_client.get_authorization_url()
    if format == "json":
        return {"authorization_url": url}
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
async def google_oauth_callback(code: str, state: str | None = None) -> dict:
    """OAuth redirect target. Exchanges the code and stores the refresh token."""
    return gmail_client.exchange_code(code, state)


@router.get("/status")
async def auth_status() -> dict:
    """Whether a usable Gmail credential exists. Never returns the token itself."""
    return gmail_client.connection_status()


@router.post("/disconnect")
async def disconnect() -> dict:
    """Forgets the stored credential. Revoke access in Google as well if needed."""
    return gmail_client.disconnect()
