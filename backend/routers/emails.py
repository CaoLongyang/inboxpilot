from __future__ import annotations

from fastapi import APIRouter

from backend.schemas import EmailDetail, EmailListResponse
from backend.services import gmail_client

router = APIRouter(prefix="/emails", tags=["emails"])


@router.get("", response_model=EmailListResponse)
async def list_emails(max_results: int = 10, q: str | None = None) -> EmailListResponse:
    """Recent emails (FR-02, task A5)."""
    items = gmail_client.list_emails(max_results=max_results, query=q)
    return EmailListResponse(emails=items, count=len(items))


@router.get("/{message_id}", response_model=EmailDetail)
async def get_email(message_id: str) -> EmailDetail:
    """One email with its body (FR-02, task A5)."""
    return EmailDetail(**gmail_client.get_email(message_id))
