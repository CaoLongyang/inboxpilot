from __future__ import annotations

from fastapi import APIRouter

from backend.schemas import AnalyzeRequest, AnalyzeResponse
from backend.services import analyzer, gmail_client

router = APIRouter(tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_email(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Structured email understanding via Nemotron (FR-03/04/05, task A8).

    Declared sync on purpose: the Gmail read and the model call both block, and
    an `async def` would run them on the event loop, freezing every other
    request (including /health) for the length of the inference call.

    Every quote in the response has been checked against the email body; anything
    the model invented is already removed and reported in `uncertainties`.
    """
    email = gmail_client.get_email(payload.message_id)
    analysis, usage, _audit = analyzer.analyse(email)
    # `cached` stays false until A9 adds the SQLite cache.
    return AnalyzeResponse(analysis=analysis, usage=usage, cached=False)
