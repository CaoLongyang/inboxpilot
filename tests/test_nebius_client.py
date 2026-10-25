"""Tests for the Nebius client.

The real API key only arrives on Day 1, so the transport is faked here. What is
under test is our own logic: config guards, usage accounting, error mapping, and
the JSON extraction that every analysis depends on.

Nothing here makes a network call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.config import Settings
from backend.errors import ConfigError, UpstreamError
from backend.services import nebius_client


class FakeClient:
    """Minimal stand-in for openai.OpenAI."""

    def __init__(self, content: str = '{"ok": true}', models=("model-a",), raises: Exception | None = None):
        self._content = content
        self._raises = raises
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(list=self._list_models)
        self._models = models

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22, total_tokens=33),
        )

    def _list_models(self):
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in self._models])


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "nebius_api_key": "test-key",
        "nebius_base_url": "https://api.example.invalid/v1/",
        "nebius_model_fast": "fast-model",
        "nebius_model_reasoning": "reason-model",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake(monkeypatch):
    """Install a fake client and configured settings; return the fake."""
    client = FakeClient()
    monkeypatch.setattr(nebius_client, "get_settings", lambda: _settings())
    monkeypatch.setattr(nebius_client, "_build_client", lambda: client)
    return client


def test_chat_requires_api_key(monkeypatch) -> None:
    monkeypatch.setattr(nebius_client, "get_settings", lambda: _settings(nebius_api_key=""))
    with pytest.raises(ConfigError) as exc:
        nebius_client.chat([{"role": "user", "content": "hi"}])
    assert exc.value.details["env_var"] == "NEBIUS_API_KEY"


def test_chat_requires_a_model_for_the_role(monkeypatch) -> None:
    monkeypatch.setattr(nebius_client, "get_settings", lambda: _settings(nebius_model_reasoning=""))
    with pytest.raises(ConfigError) as exc:
        nebius_client.chat([{"role": "user", "content": "hi"}], role="reasoning")
    assert exc.value.details["expected_env_var"] == "NEBIUS_MODEL_REASONING"


def test_chat_records_model_latency_and_tokens(fake) -> None:
    text, usage = nebius_client.chat([{"role": "user", "content": "hi"}])
    assert text == '{"ok": true}'
    assert usage.model == "fast-model"
    assert usage.latency_ms >= 0
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 22, 33)


def test_chat_uses_the_reasoning_model_for_that_role(fake) -> None:
    nebius_client.chat([{"role": "user", "content": "hi"}], role="reasoning")
    assert fake.calls[0]["model"] == "reason-model"


def test_chat_is_deterministic_by_default(fake) -> None:
    nebius_client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls[0]["temperature"] == 0.0


def test_chat_maps_transport_failure_to_upstream_error(monkeypatch) -> None:
    client = FakeClient(raises=RuntimeError("connection reset"))
    monkeypatch.setattr(nebius_client, "get_settings", lambda: _settings())
    monkeypatch.setattr(nebius_client, "_build_client", lambda: client)
    with pytest.raises(UpstreamError) as exc:
        nebius_client.chat([{"role": "user", "content": "hi"}])
    assert "connection reset" in exc.value.details["reason"]


def test_list_models_returns_ids(fake) -> None:
    assert nebius_client.list_models() == ["model-a"]


def test_parse_json_object_accepts_a_plain_object() -> None:
    assert nebius_client.parse_json_object('{"a": 1}') == {"a": 1}


def test_parse_json_object_strips_a_markdown_fence() -> None:
    assert nebius_client.parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_object_finds_an_object_inside_prose() -> None:
    text = 'Sure, here is the result:\n{"category": "action_required"}\nHope that helps.'
    assert nebius_client.parse_json_object(text) == {"category": "action_required"}


def test_parse_json_object_rejects_a_bare_list() -> None:
    with pytest.raises(UpstreamError):
        nebius_client.parse_json_object("[1, 2, 3]")


def test_parse_json_object_rejects_unparseable_output() -> None:
    with pytest.raises(UpstreamError):
        nebius_client.parse_json_object("I cannot answer that.")


def test_chat_json_returns_the_parsed_object(fake) -> None:
    payload, usage = nebius_client.chat_json([{"role": "user", "content": "hi"}])
    assert payload == {"ok": True}
    assert usage.total_tokens == 33
