"""One-command check that the stored Gmail credential actually works.

Usage:
    .\\.venv\\Scripts\\python.exe -m scripts.verify_gmail

Prints PASS/FAIL and nothing secret: no token, no client secret, no email body.
Run it after every re-authorization, and once a week during the judging window.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Work whether run as `python -m scripts.verify_gmail` or as a plain file.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.config import get_settings  # noqa: E402
from backend.services import gmail_client, token_store  # noqa: E402

logging.disable(logging.WARNING)

OK = "  [ok]  "
BAD = "  [FAIL]"


def _mask(email: str | None) -> str:
    return email or "(unknown)"


def _token_age_days() -> float | None:
    path: Path = get_settings().db_path.parent / "token.json"
    if not path.exists():
        return None
    issued = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(tz=timezone.utc) - issued).total_seconds() / 86400


def main() -> int:
    settings = get_settings()
    print("InboxPilot - Gmail credential check\n")

    if not settings.is_google_configured:
        print(BAD, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured")
        return 1
    print(OK, "OAuth client configured")

    state = token_store.status()
    if not state["connected"]:
        print(BAD, "No stored credential. Open http://localhost:8000/auth/google")
        return 1
    print(OK, f"Credential present (source={state['source']}, account={_mask(state['email'])})")

    age = _token_age_days()
    if state["source"] == "file" and age is not None:
        print(f"         token file age: {age:.1f} days")
        if age >= 6.5:
            print(BAD, "A Testing-mode token expires at 7 days. Re-authorize now.")

    try:
        service = gmail_client._service()
        profile = service.users().getProfile(userId="me").execute()
    except Exception as exc:  # noqa: BLE001 - report, do not crash
        print(BAD, f"Token refresh or profile call failed: {type(exc).__name__}: {exc}")
        print("         The credential is dead. Re-authorize from /auth/google.")
        return 1

    print(OK, f"Refresh succeeded for {profile.get('emailAddress')}")
    print(f"         messages: {profile.get('messagesTotal')}, threads: {profile.get('threadsTotal')}")

    try:
        emails = gmail_client.list_emails(max_results=3)
    except Exception as exc:  # noqa: BLE001
        print(BAD, f"messages.list failed: {type(exc).__name__}: {exc}")
        return 1

    print(OK, f"messages.list returned {len(emails)} message(s)")
    for item in emails:
        subject = (item.subject or "(no subject)")[:52]
        print(f"         - {subject}")

    print("\nPASS - the Gmail read path is working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
