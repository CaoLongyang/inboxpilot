"""Nebius Token Factory client.

Token Factory exposes an OpenAI-compatible chat completions API, so the
official `openai` SDK is used with a custom base_url.

Two model roles (see hackathon Track 2 guidance):
- `fast`      -> classification / summary / extraction  (Nemotron Super or Nano)
- `reasoning` -> WhatIf decision trees and planning      (Nemotron Ultra)

Base URL is `https://api.tokenfactory.nebius.com/v1/` per the official
Token Factory quickstart. Model IDs drift as the catalogue changes, so always
verify them with `python -m scripts.check_setup` instead of trusting this file.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from backend.config import get_settings
from backend.errors import ConfigError, UpstreamError
from backend.schemas import ModelUsage

log = logging.getLogger(__name__)


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.nebius_api_key and settings.nebius_base_url)


def _build_client():
    settings = get_settings()
    if not settings.nebius_api_key:
        raise ConfigError(
            "Nebius API key is not configured",
            details={"env_var": "NEBIUS_API_KEY"},
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency check
        raise ConfigError("The 'openai' package is not installed") from exc

    return OpenAI(
        base_url=settings.nebius_base_url,
        api_key=settings.nebius_api_key,
        timeout=settings.nebius_timeout_seconds,
        max_retries=settings.nebius_max_retries,
    )


def list_models() -> list[str]:
    """Available model IDs. Used once on Day 1 to fill the .env file."""
    client = _build_client()
    try:
        response = client.models.list()
    except Exception as exc:  # noqa: BLE001 - third party
        raise UpstreamError("Could not list Nebius models", details={"reason": str(exc)}) from exc
    return [m.id for m in response.data]


def chat(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    role: str = "fast",
    temperature: float = 0.0,
    max_tokens: int = 1200,
) -> tuple[str, ModelUsage]:
    """One chat completion. Returns (text, usage)."""
    settings = get_settings()
    chosen = model or (
        settings.nebius_model_reasoning if role == "reasoning" else settings.nebius_model_fast
    )
    if not chosen:
        raise ConfigError(
            "No Nebius model configured",
            details={"expected_env_var": f"NEBIUS_MODEL_{role.upper()}"},
        )

    client = _build_client()
    started = time.perf_counter()
    try:
        completion = client.chat.completions.create(
            model=chosen,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - third party
        raise UpstreamError(
            "The model call failed",
            details={"model": chosen, "reason": str(exc)},
        ) from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = completion.usage
    meta = ModelUsage(
        model=chosen,
        latency_ms=latency_ms,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )
    # Deliberately do not log prompt content: it contains email bodies.
    log.info(
        "nebius.chat model=%s latency_ms=%s tokens=%s",
        meta.model,
        meta.latency_ms,
        meta.total_tokens,
    )
    text = (completion.choices[0].message.content or "").strip()
    return text, meta


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating code fences."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```", 2)[1] if candidate.count("```") >= 2 else candidate
        if candidate.lstrip().lower().startswith("json"):
            candidate = candidate.lstrip()[4:]
        candidate = candidate.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            raise UpstreamError("The model did not return a JSON object") from None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise UpstreamError("The model returned malformed JSON") from exc

    if not isinstance(parsed, dict):
        raise UpstreamError("The model returned JSON that is not an object")
    return parsed


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    role: str = "fast",
    max_tokens: int = 1200,
) -> tuple[dict[str, Any], ModelUsage]:
    text, usage = chat(
        messages, model=model, role=role, temperature=0.0, max_tokens=max_tokens
    )
    return parse_json_object(text), usage


if __name__ == "__main__":  # pragma: no cover - manual smoke test (A2)
    logging.basicConfig(level=logging.INFO)
    print("configured:", is_configured())
    if is_configured():
        print("models:", list_models()[:20])
