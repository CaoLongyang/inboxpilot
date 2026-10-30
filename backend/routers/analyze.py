from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.schemas import AnalyzeRequest, AnalyzeResponse, CachedAnalysis
from backend.services import analyzer, gmail_client, store, token_store

router = APIRouter(tags=["analysis"])

log = logging.getLogger(__name__)


@router.get("/analyses/{message_id}", response_model=CachedAnalysis)
def read_cached_analysis(message_id: str) -> CachedAnalysis:
    """Return an analysis that already exists, or an empty answer.

    The background watcher analyses mail before anyone asks for it, so the
    analysis page calls this on open: a message the watcher has already read
    appears instantly instead of waiting for a model round trip. Reading the
    cache must never trigger inference, which is why this is a separate route
    from /analyze — /analyze would analyse on a miss, and a page that costs
    money to open is not a page you can click around during a demo.
    """
    hit = store.cache_lookup(message_id)
    if hit is None:
        return CachedAnalysis()
    analysis, usage = hit
    return CachedAnalysis(analysis=analysis, usage=usage, cached=True)


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_email(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Structured email understanding via Nemotron (FR-03/04/05, tasks A8 + A9).

    Declared sync on purpose: the Gmail read and the model call both block, and
    an `async def` would run them on the event loop, freezing every other
    request (including /health) for the length of the inference call.

    Every quote in the response has been checked against the email body; anything
    the model invented is already removed and reported in `uncertainties`.

    Caching: an identical `message_id` returns the stored analysis with
    `cached: true` and does not touch Gmail or Nebius at all. That matters twice
    over — the second click is instant, and a reviewer clicking around the demo
    cannot accidentally spend the credit budget. `force_refresh: true` re-runs
    the model and replaces the row.
    """
    if not payload.force_refresh:
        hit = store.cache_lookup(payload.message_id)
        if hit is not None:
            cached_analysis, cached_usage = hit
            log.info("analyze.cache_hit message_id=%s", payload.message_id)
            return AnalyzeResponse(
                analysis=cached_analysis, usage=cached_usage, cached=True
            )

    email = gmail_client.get_email(payload.message_id)
    analysis, usage, _audit = analyzer.analyse(email)

    # Best effort: `cache_store` swallows storage failures, because the user
    # already waited for the model and losing the cache must not lose the answer.
    store.cache_store(analysis, usage, account=token_store.status().get("email") or "")

    return AnalyzeResponse(analysis=analysis, usage=usage, cached=False)
