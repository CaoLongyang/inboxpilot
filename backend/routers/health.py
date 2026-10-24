from __future__ import annotations

from fastapi import APIRouter

from backend.config import get_settings
from backend.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness + configuration check.

    Deliberately does not call Nebius or Google: this endpoint is polled by the
    hosting platform and by our own uptime monitor during the judging period.
    It reports only whether credentials are present, never their values.
    """
    settings = get_settings()
    integrations = {
        "nebius": settings.is_nebius_configured,
        "google_oauth": settings.is_google_configured,
        "demo_account": settings.is_demo_account_configured,
        "email_send_enabled": settings.allow_email_send,
    }
    ready = integrations["nebius"] and integrations["google_oauth"]
    return HealthResponse(
        status="ok" if ready else "degraded",
        version="0.1.0",
        env=settings.app_env,
        integrations=integrations,
    )
