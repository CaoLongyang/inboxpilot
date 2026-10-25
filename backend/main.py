"""InboxPilot backend.

Run locally:
    uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import get_settings
from backend.errors import AppError
from backend.routers import actions, analyze, auth, emails, health
from backend.services import store

VERSION = "0.1.0"

log = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Every response carries a request id, and every error message can be traced."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
    request_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.log_level)

    app = FastAPI(
        title="InboxPilot API",
        version=VERSION,
        description="Email-to-action agent: read mail, analyze with Nemotron, "
        "approve an action, create a real Gmail draft.",
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.warning("app_error code=%s status=%s request_id=%s", exc.code, exc.status_code, request_id)
        return _error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
            request_id=request_id,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            status_code=422,
            code="validation_error",
            message="The request body does not match the API schema",
            details={"errors": exc.errors()},
            request_id=getattr(request.state, "request_id", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        # Log the type only; never the payload, which may contain email content.
        log.exception("unhandled_error type=%s request_id=%s", type(exc).__name__, request_id)
        return _error_response(
            status_code=500,
            code="internal_error",
            message="Something went wrong on our side. Please retry.",
            request_id=request_id,
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(emails.router)
    app.include_router(analyze.router)
    app.include_router(actions.router)

    # Create the SQLite tables at boot (task A9). Failures are logged, not
    # raised: a read-only disk degrades /analyze to "no cache", it does not stop
    # the service from serving the endpoints a judge will click.
    store.try_init_db()

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": "InboxPilot API",
            "version": VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()
