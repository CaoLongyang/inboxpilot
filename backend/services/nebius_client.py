"""Nebius Token Factory client.

Token Factory exposes an OpenAI-compatible chat completions API, so the
official `openai` SDK is used with a custom base_url.

Two model roles (see hackathon Track 2 guidance):
- `fast`      -> classification / summary / extraction  (Nemotron Super or Nano)
- `reasoning` -> WhatIf decision trees and planning      (Nemotron Ultra)

Nemotron is a hybrid-reasoning family: it can emit a visible chain of thought
before the answer. `fast` turns that off (an extraction task has nothing to
reason about, and the thinking tokens are billed), `reasoning` leaves it on.

Base URL is `https://api.tokenfactory.nebius.com/v1/` per the official
Token Factory quickstart. Model IDs drift as the catalogue changes, so always
verify them with `python scripts/check_nebius.py` instead of trusting this file.
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


def _thinking_default(role: str) -> bool:
    """Extraction wants an answer; planning wants a chain of thought.

    The Nemotron family on Token Factory exposes a hybrid thinking mode. With it
    on, the model writes to `reasoning_content` first and only then to `content`,
    so an extraction call can burn its whole `max_tokens` budget thinking and
    return `content=None`. Measured on `nemotron-3-super-120b-a12b`:
    2793ms / 140 tokens with thinking, 1088ms / 81 tokens without.

    Reading an image is extraction too: the timetable is either legible or it
    is not, and thinking about it does not make it more legible.
    """
    return role == "reasoning"


def _model_for(role: str) -> str:
    settings = get_settings()
    if role == "reasoning":
        return settings.nebius_model_reasoning
    if role == "vision":
        return settings.nebius_model_vision
    return settings.nebius_model_fast


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    role: str = "fast",
    temperature: float = 0.0,
    max_tokens: int = 2000,
    thinking: bool | None = None,
    response_format: dict | None = None,
) -> tuple[str, ModelUsage]:
    """One chat completion. Returns (text, usage)."""
    settings = get_settings()
    chosen = model or _model_for(role)
    if not chosen:
        raise ConfigError(
            "No Nebius model configured",
            details={"expected_env_var": f"NEBIUS_MODEL_{role.upper()}"},
        )

    use_thinking = _thinking_default(role) if thinking is None else thinking
    request: dict[str, Any] = {
        "model": chosen,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format is not None:
        request["response_format"] = response_format
    if not use_thinking:
        # vLLM reads this out of the request body; the OpenAI SDK needs
        # `extra_body` to forward keys it does not know about.
        request["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    client = _build_client()
    started = time.perf_counter()
    try:
        completion = client.chat.completions.create(**request)
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
        "nebius.chat model=%s role=%s thinking=%s latency_ms=%s tokens=%s",
        meta.model,
        role,
        use_thinking,
        meta.latency_ms,
        meta.total_tokens,
    )

    choice = completion.choices[0]
    text = (choice.message.content or "").strip()

    if not text:
        # A reasoning model that ran out of budget returns `content=None` with
        # the whole answer still inside `reasoning_content`. Returning "" here
        # would surface as a confusing "the model did not return a JSON
        # object", so name the actual cause instead. Checked before truncation
        # because "it spent the whole budget thinking" is the actionable fact.
        reasoning = (
            getattr(choice.message, "reasoning_content", None)
            or getattr(choice.message, "reasoning", None)
            or ""
        )
        raise UpstreamError(
            "The model returned no answer text",
            details={
                "model": chosen,
                "finish_reason": choice.finish_reason,
                "reasoning_chars": len(reasoning),
                "reason": "reasoning_only_response" if reasoning else "empty_response",
            },
        )

    # Hitting the token ceiling mid-object produces text that looks like JSON
    # right up to the point it doesn't parse. Report the cause, not the symptom:
    # "malformed JSON" would send the next person hunting for a prompt bug.
    if choice.finish_reason == "length":
        raise UpstreamError(
            "The model ran out of output tokens before finishing",
            details={
                "model": chosen,
                "reason": "output_truncated",
                "max_tokens": max_tokens,
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "chars": len(text),
            },
        )

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
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    role: str = "fast",
    max_tokens: int = 2000,
    thinking: bool | None = None,
) -> tuple[dict[str, Any], ModelUsage]:
    """A chat completion that must be one JSON object.

    One retry, only for truncation. Measured on this exact prompt, the small
    Nemotron variants spend 1195 of a 1200-token budget, so the ceiling is a
    real failure mode rather than a theoretical one; doubling the budget once
    costs a single extra call on a rare path. A malformed-but-complete response
    is *not* retried — that is the model getting the task wrong, and quietly
    asking again until it produces something parseable is how a pipeline ends up
    with output nobody can explain.
    """
    try:
        text, usage = chat(
            messages,
            model=model,
            role=role,
            temperature=0.0,
            max_tokens=max_tokens,
            thinking=thinking,
            response_format={"type": "json_object"},
        )
    except UpstreamError as exc:
        if exc.details.get("reason") != "output_truncated":
            raise
        log.warning(
            "nebius.chat_json retrying after truncation at max_tokens=%s", max_tokens
        )
        text, usage = chat(
            messages,
            model=model,
            role=role,
            temperature=0.0,
            max_tokens=max_tokens * 2,
            thinking=thinking,
            response_format={"type": "json_object"},
        )
    return parse_json_object(text), usage


if __name__ == "__main__":  # pragma: no cover - manual smoke test (A2)
    logging.basicConfig(level=logging.INFO)
    print("configured:", is_configured())
    if is_configured():
        print("models:", list_models()[:20])
