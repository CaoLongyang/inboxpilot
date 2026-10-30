"""The weekly digest endpoint.

The important behaviour is what happens without `refresh=true`: it reads the
cache and stops. The page calls this on open, and a GET that quietly ran the
model would make navigating to the page cost money every time — the same reason
`/analyses/{id}` exists separately from `/analyze`.

Generating is therefore an explicit act, and the button that triggers it says so.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from backend.schemas import DigestResponse
from backend.services import digest, store

router = APIRouter(tags=["digest"])

log = logging.getLogger(__name__)


@router.get("/digest", response_model=DigestResponse)
def read_digest(
    days: int = Query(default=digest.DEFAULT_DAYS, ge=1, le=30),
    refresh: bool = Query(default=False),
) -> DigestResponse:
    """Return a stored digest, or an empty answer when there is none.

    `refresh=true` reads the inbox and generates a new one. Without it this is a
    single SQLite read, which is why the page can call it on every rerun.
    """
    if not refresh:
        cached = store.load_digest(days)
        if cached is not None:
            log.info("digest.cache_hit days=%d", days)
            return DigestResponse(digest=cached, cached=True)
        return DigestResponse()

    return DigestResponse(digest=digest.generate(days))
