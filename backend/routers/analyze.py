from __future__ import annotations

from fastapi import APIRouter

from backend.schemas import AnalyzeRequest, AnalyzeResponse

router = APIRouter(tags=["analysis"])


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_email(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Structured email understanding via Nemotron (FR-03/04/05, task A8).

    Not implemented yet. When it lands:
    - the email body is treated as untrusted data, never as instructions
    - the response is validated against EmailAnalysis before it is returned
    - the model name, latency and token usage are recorded for every call
    """
    from backend.errors import NotImplementedYet

    raise NotImplementedYet("POST /analyze lands on Day 3 (task A8)")
