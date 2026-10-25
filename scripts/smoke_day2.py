"""Day 2 acceptance check for the Gmail read path.

Run this against a live backend, in the same way the Streamlit UI talks to it:

    # terminal 1
    .\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8000
    # terminal 2
    .\\.venv\\Scripts\\python.exe -m scripts.smoke_day2

It prints one line per check, never prints a token or an email body, and exits
non-zero if anything fails, so it can also be used in CI.
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.getenv("INBOXPILOT_API_URL", "http://127.0.0.1:8000").rstrip("/")
# The dev machine sets http_proxy, which would otherwise intercept localhost.
client = httpx.Client(base_url=BASE, timeout=30.0, trust_env=False)

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((ok, name, detail))
    print(f"  {'PASS' if ok else 'FAIL':4}  {name:38} {detail}")
    return ok


def skip(name: str, reason: str) -> None:
    results.append((True, name, f"skipped: {reason}"))
    print(f"  SKIP  {name:38} {reason}")


def main() -> int:
    print(f"InboxPilot - Day 2 acceptance check\nBackend: {BASE}\n")

    try:
        health = client.get("/health")
    except httpx.RequestError as exc:
        print(f"  Cannot reach the backend: {exc}")
        print("\n  Start it first:")
        print("    .\\.venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8000")
        return 1

    body = health.json()
    integrations = body.get("integrations", {})
    check(
        "GET /health",
        health.status_code == 200 and body.get("status") in {"ok", "degraded"},
        f"status={body.get('status')} google={integrations.get('google_oauth')} "
        f"nebius={integrations.get('nebius')}",
    )

    status = client.get("/auth/status").json()
    connected = bool(status.get("connected"))
    check("GET /auth/status", "refresh_token" not in status, f"connected={connected}")
    if not connected:
        print("\n  Gmail is not connected. Open http://localhost:8000/auth/google first.")
        return 1

    listing = client.get("/emails", params={"max_results": 3})
    emails = listing.json().get("emails", [])
    check("GET /emails", listing.status_code == 200 and len(emails) > 0, f"{len(emails)} email(s)")
    if not emails:
        return 1

    message_id = emails[0]["id"]
    detail = client.get(f"/emails/{message_id}")
    payload = detail.json()
    has_body = bool(payload.get("body_text")) or bool(payload.get("body_html"))
    check(
        "GET /emails/{id}",
        detail.status_code == 200 and "subject" in payload,
        f"{len(payload.get('body_text') or '')} chars, html={'yes' if payload.get('body_html') else 'no'}",
    )
    check("  body is not empty", has_body, "text or html present")

    thread = client.get(f"/emails/{message_id}/thread")
    thread_body = thread.json()
    check(
        "GET /emails/{id}/thread",
        thread.status_code == 200 and isinstance(thread_body, list) and len(thread_body) >= 1,
        f"{len(thread_body) if isinstance(thread_body, list) else '?'} message(s)",
    )

    clamped = client.get("/emails", params={"max_results": 500}).json()
    check(
        "GET /emails?max_results=500",
        clamped.get("count", 999) <= 20,
        f"returned {clamped.get('count')} (server clamps to 20)",
    )

    missing = client.get("/emails/definitely-not-a-real-id")
    error = missing.json().get("error", {})
    check(
        "GET /emails/{unknown}",
        missing.status_code == 404 and error.get("code") == "not_found",
        f"HTTP {missing.status_code} code={error.get('code')}",
    )

    # The frontend must never be able to see the credential.
    leaked = "refresh_token" in detail.text or "refresh_token" in status.__str__()
    check("no credential in responses", not leaked, "refresh_token absent")

    passed = sum(1 for ok, _, _ in results if ok)
    total = len(results)
    print(f"\n{'PASS' if passed == total else 'FAIL'}  {passed}/{total} checks")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
