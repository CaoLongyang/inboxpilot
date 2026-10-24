from __future__ import annotations

from fastapi import APIRouter

from backend.errors import NotImplementedYet
from backend.services import gmail_client

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google")
async def start_google_oauth() -> dict:
    """Returns the Google consent URL the frontend should open (FR-01, task A3)."""
    return {"authorization_url": gmail_client.get_authorization_url()}


@router.get("/google/callback")
async def google_oauth_callback(code: str, state: str | None = None) -> dict:
    """OAuth redirect target. Exchanges the code for a refresh token (task A3)."""
    gmail_client.exchange_code(code)
    return {"connected": True}


@router.get("/status")
async def auth_status() -> dict:
    """Whether a usable Gmail credential exists. Never returns the token itself."""
    return gmail_client.connection_status()


@router.post("/disconnect")
async def disconnect() -> dict:
    """Revokes the stored credential and clears local state (task A3)."""
    raise NotImplementedYet("Disconnect lands with task A3")
