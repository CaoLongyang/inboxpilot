"""Unified error model.

Every failure returned by the API has the same shape:

    {"error": {"code": "...", "message": "...", "details": {...}, "request_id": "..."}}

`code` is stable and machine-readable (the frontend switches on it).
`message` is safe to show to a user.
Never put secrets or full email bodies into `details`.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected, user-facing failures."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class ConfigError(AppError):
    status_code = 500
    code = "configuration_error"


class AuthError(AppError):
    status_code = 401
    code = "not_authenticated"


class ApprovalRequired(AppError):
    status_code = 403
    code = "approval_required"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class UpstreamError(AppError):
    """A third-party call (Nebius, Gmail, Google OAuth) failed."""

    status_code = 502
    code = "upstream_error"


class DuplicateRequest(AppError):
    status_code = 409
    code = "duplicate_request"


class NotImplementedYet(AppError):
    status_code = 501
    code = "not_implemented"
