from __future__ import annotations

from fastapi import APIRouter

from backend.schemas import EmailDetail, EmailListResponse
from backend.services import gmail_client

router = APIRouter(prefix="/emails", tags=["emails"])


@router.get("", response_model=EmailListResponse)
async def list_emails(max_results: int = 10, q: str | None = None) -> EmailListResponse:
    """Recent emails (FR-02, task A5/A6).

    `max_results` is clamped server-side to MAX_EMAILS_PER_REQUEST, so a client
    cannot pull the whole mailbox in a single call.
    """
    items = gmail_client.list_emails(max_results=max_results, query=q)
    return EmailListResponse(emails=items, count=len(items))


@router.get("/{message_id}", response_model=EmailDetail)
async def get_email(message_id: str) -> EmailDetail:
    """One email with its body (FR-02, task A5/A6)."""
    # get_email already returns an EmailDetail. Do not re-wrap it: pydantic
    # models are not mappings, so `EmailDetail(**model)` raises TypeError.
    return gmail_client.get_email(message_id)


@router.get("/{message_id}/thread", response_model=list[EmailDetail])
async def get_thread(message_id: str) -> list[EmailDetail]:
    """Every message in the thread this message belongs to (task A5).

    Takes a message id rather than a thread id so the caller never has to know
    the thread layout first. A single-message thread returns one item.
    """
    thread_id = gmail_client.get_email(message_id).thread_id or message_id
    return gmail_client.get_thread(thread_id)
