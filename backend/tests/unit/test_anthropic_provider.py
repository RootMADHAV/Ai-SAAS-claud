"""Unit tests for AnthropicProvider.

Mirrors test_minio_storage.py's own pattern for wrapping an official
SDK: the SDK's client class is monkeypatched at construction time with a
fake that returns canned data, rather than making a real network call to
the provider -- see PROJECT_STATE.md's Milestone 6 technical-debt entry
on why this, not a real Anthropic API call, is this milestone's
verification tier for this adapter (no real credential is available in
this environment, the same constraint already documented for
MinioStoragePort/NucleiAdapter in Milestones 3-4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from anthropic.types import TextBlock

from app.application.interfaces.ai_provider_port import AIProviderError
from app.infrastructure.ai_providers.anthropic_provider import AnthropicProvider


@dataclass
class _FakeMessagesResource:
    response: object | None = None
    error: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class _FakeAsyncAnthropic:
    def __init__(self, *, messages: _FakeMessagesResource, **_: object) -> None:
        self.messages = messages


def _text_block(text: str) -> TextBlock:
    return TextBlock(text=text, type="text")


def _fake_message(*, content: list[object], model: str = "claude-sonnet-4-5") -> object:
    return SimpleNamespace(content=content, model=model, stop_reason="end_turn")


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, messages: _FakeMessagesResource) -> None:
    monkeypatch.setattr(
        "app.infrastructure.ai_providers.anthropic_provider.anthropic.AsyncAnthropic",
        lambda **kwargs: _FakeAsyncAnthropic(messages=messages),
    )


@pytest.mark.asyncio
async def test_provider_name_is_anthropic() -> None:
    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    assert provider.provider_name == "anthropic"


@pytest.mark.asyncio
async def test_complete_returns_text_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = _FakeMessagesResource(
        response=_fake_message(content=[_text_block("hello")], model="claude-sonnet-4-5")
    )
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    result = await provider.complete(system_prompt="sys", user_prompt="user")

    assert result.text == "hello"
    assert result.model == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_complete_concatenates_multiple_text_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = _FakeMessagesResource(
        response=_fake_message(content=[_text_block("hello "), _text_block("world")])
    )
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    result = await provider.complete(system_prompt="sys", user_prompt="user")

    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_complete_passes_system_and_user_prompt_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _FakeMessagesResource(response=_fake_message(content=[_text_block("ok")]))
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    await provider.complete(system_prompt="be terse", user_prompt="analyze this")

    assert len(messages.calls) == 1
    call = messages.calls[0]
    assert call["system"] == "be terse"
    assert call["messages"] == [{"role": "user", "content": "analyze this"}]
    assert call["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_complete_raises_ai_provider_error_on_no_text_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _FakeMessagesResource(response=_fake_message(content=[]))
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    with pytest.raises(AIProviderError, match="no text content"):
        await provider.complete(system_prompt="sys", user_prompt="user")


@pytest.mark.asyncio
async def test_complete_translates_authentication_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_response = httpx.Response(status_code=401, request=fake_request)
    error = anthropic.AuthenticationError(
        message="invalid x-api-key", response=fake_response, body=None
    )
    messages = _FakeMessagesResource(error=error)
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="bad-key", model="claude-sonnet-4-5")
    with pytest.raises(AIProviderError, match="Anthropic API error"):
        await provider.complete(system_prompt="sys", user_prompt="user")


@pytest.mark.asyncio
async def test_complete_translates_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=fake_request)
    messages = _FakeMessagesResource(error=error)
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    with pytest.raises(AIProviderError, match="Anthropic API error"):
        await provider.complete(system_prompt="sys", user_prompt="user")


@pytest.mark.asyncio
async def test_non_api_error_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine bug (not a provider failure) must still propagate --
    AnthropicProvider only translates anthropic.APIError, matching
    _ai_analyze's own "don't swallow unexpected exceptions" rule."""
    messages = _FakeMessagesResource(error=ValueError("some unrelated bug"))
    _install_fake_client(monkeypatch, messages)

    provider = AnthropicProvider(api_key="test-key", model="claude-sonnet-4-5")
    with pytest.raises(ValueError, match="some unrelated bug"):
        await provider.complete(system_prompt="sys", user_prompt="user")
