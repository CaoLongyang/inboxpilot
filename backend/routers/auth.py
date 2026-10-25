from __future__ import annotations

import html
import logging
from string import Template

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from backend.config import get_settings
from backend.errors import AppError
from backend.services import gmail_client

router = APIRouter(prefix="/auth", tags=["auth"])

log = logging.getLogger(__name__)

# The OAuth callback is the one endpoint a human reaches in a browser, usually
# mid-demo. A raw JSON error there looks broken even when the backend behaved
# correctly, so failures render as a page instead.
_PAGE = Template(
    """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title - InboxPilot</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; padding: 24px;
         font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f6f7f9; color: #1f2328; }
  .card { max-width: 540px; padding: 28px 32px; background: #fff;
          border-radius: 14px; box-shadow: 0 6px 24px rgba(16, 24, 40, .08); }
  h1 { font-size: 18px; margin: 0 0 10px; }
  p { margin: 0 0 12px; color: #4a5260; }
  code { background: #f0f2f5; padding: 2px 6px; border-radius: 6px; font-size: 13px; }
  a { color: #2563eb; text-decoration: none; font-weight: 600; }
  @media (prefers-color-scheme: dark) {
    body { background: #15171a; color: #e6e8eb; }
    .card { background: #1d2024; border: 1px solid #2b3036; box-shadow: none; }
    p { color: #a8b0ba; }
    code { background: #2b3036; }
    a { color: #7aa2ff; }
  }
</style>
</head>
<body>
  <div class="card">
    <h1>$title</h1>
    <p>$message</p>
    $detail
    <p><a href="$back">Back to InboxPilot</a></p>
  </div>
</body>
</html>"""
)


def _error_page(status_code: int, title: str, message: str, detail: str = "") -> HTMLResponse:
    body = _PAGE.substitute(
        title=html.escape(title),
        message=html.escape(message),
        detail=detail,
        back=html.escape(get_settings().frontend_url),
    )
    return HTMLResponse(body, status_code=status_code)


@router.get("/google")
async def start_google_oauth(format: str = Query("redirect", pattern="^(redirect|json)$")):
    """Start the Gmail OAuth flow (FR-01, task A3).

    Default: 302 to Google's consent screen, which is what a browser should hit.
    `?format=json` returns the URL instead, for the Streamlit frontend.
    """
    url = gmail_client.get_authorization_url()
    if format == "json":
        return {"authorization_url": url}
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback", response_model=None)
def google_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> HTMLResponse | RedirectResponse:
    """OAuth redirect target. Exchanges the code and stores the refresh token.

    Declared sync: the code exchange is a blocking HTTPS call.

    Three outcomes, all of which a real user can hit:
    - consent granted -> 302 back to the Streamlit app, which now shows
      "Connected". The browser must never be left on an API response.
    - consent denied (`?error=access_denied`) -> a readable page, not a 422.
    - exchange failed -> a readable page carrying the stable error code and the
      request id, so the failure can be traced from a screenshot.
    """
    if error:
        return _error_page(
            400,
            "Gmail access was not granted",
            "Google reported that the authorization request was cancelled or "
            "denied. InboxPilot needs read-only Gmail access to work, so it "
            "cannot continue without it.",
            f"<p>Google said: <code>{html.escape(error)}</code></p>",
        )

    if not code:
        return _error_page(
            400,
            "Missing authorization code",
            "The callback was reached without a code, which usually means the "
            "URL was opened directly instead of through the Google consent screen.",
        )

    try:
        gmail_client.exchange_code(code, state)
    except AppError as exc:
        request_id = getattr(request.state, "request_id", None) or "unknown"
        # `details` normally stays server-side. The one exception is a missing
        # setting: naming the variable is the entire value of the message, and
        # a variable name is not a secret.
        hint = ""
        if exc.code == "configuration_error" and exc.details.get("env_var"):
            hint = (
                "<p>Missing setting: "
                f"<code>{html.escape(str(exc.details['env_var']))}</code></p>"
            )
        return _error_page(
            exc.status_code,
            "Could not finish connecting Gmail",
            exc.message,
            hint
            + f"<p>Error code: <code>{html.escape(exc.code)}</code> &middot; "
            f"request id: <code>{html.escape(request_id)}</code></p>",
        )
    except Exception as exc:  # noqa: BLE001 - a browser is waiting; never leak a stack trace
        request_id = getattr(request.state, "request_id", None) or "unknown"
        log.exception(
            "auth.callback_unexpected type=%s request_id=%s", type(exc).__name__, request_id
        )
        return _error_page(
            500,
            "Could not finish connecting Gmail",
            "Something went wrong while exchanging the authorization code. "
            "Try connecting again; if it keeps failing, check the backend log.",
            f"<p>request id: <code>{html.escape(request_id)}</code></p>",
        )

    return RedirectResponse(get_settings().frontend_url, status_code=302)


@router.get("/status")
async def auth_status() -> dict:
    """Whether a usable Gmail credential exists. Never returns the token itself."""
    return gmail_client.connection_status()


@router.post("/disconnect")
async def disconnect() -> dict:
    """Forgets the stored credential. Revoke access in Google as well if needed."""
    return gmail_client.disconnect()
