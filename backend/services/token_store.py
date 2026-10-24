"""Server-side storage for the Gmail refresh token.

The refresh token is the most sensitive artifact in this project: with it, the
holder can read the user's mailbox. Rules:

- It never leaves the server. The frontend only ever sees booleans.
- It is never written to logs, error details, or the repository.
- In a deployed environment it comes from `DEMO_REFRESH_TOKEN`, so a restart or
  a redeploy does not break the judge-facing demo.
- Locally it is written to `data/token.json`, which is git-ignored.

Precedence: environment variable first (deployed / judging), file second (local).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from backend.config import get_settings

log = logging.getLogger(__name__)


def _token_path() -> Path:
    return get_settings().db_path.parent / "token.json"


def load() -> dict | None:
    """Return {"refresh_token", "email", "scopes"} or None.

    Never raises: a corrupt token file must not crash the app, it should simply
    look like "not connected".
    """
    settings = get_settings()

    if settings.demo_refresh_token:
        return {
            "refresh_token": settings.demo_refresh_token,
            "email": settings.demo_account_email or None,
            "scopes": settings.google_scope_list,
            "source": "env",
        }

    path = _token_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("token_store.unreadable error=%s", type(exc).__name__)
        return None

    if not data.get("refresh_token"):
        return None
    data["source"] = "file"
    return data


def save(*, refresh_token: str, email: str | None, scopes: list[str]) -> Path:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"refresh_token": refresh_token, "email": email, "scopes": scopes}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:  # best effort on Windows, meaningful on POSIX
        os.chmod(path, 0o600)
    except OSError:
        pass
    log.info("token_store.saved path=%s", path)
    return path


def clear() -> None:
    path = _token_path()
    if path.exists():
        path.unlink()
        log.info("token_store.cleared")


def status() -> dict:
    """Booleans only. Safe to return to the frontend."""
    data = load()
    return {
        "connected": data is not None,
        "email": (data or {}).get("email"),
        "source": (data or {}).get("source"),
        "scopes": (data or {}).get("scopes", []),
    }
