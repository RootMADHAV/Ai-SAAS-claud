"""Anthropic AI provider adapter -- the first concrete ``AIProviderPort``
implementation (PROJECT_STATE.md section 2's four named providers;
Anthropic is ``Settings.ai_default_provider``'s default).

Wraps the official ``anthropic`` Python SDK's async client
(``anthropic.AsyncAnthropic``), mirroring ``MinioStoragePort``'s own
precedent of wrapping an official SDK rather than hand-rolling HTTP
calls against the provider's API (see that module's docstring in
``app/infrastructure/storage/minio_storage.py``). Unlike
``MinioStoragePort``, no ``asyncio.to_thread`` wrapping is needed here --
the ``anthropic`` SDK's async client is natively async (backed by
``httpx.AsyncClient``), so this adapter's ``complete()`` awaits it
directly.
"""

from __future__ import annotations

import anthropic
from anthropic.types import TextBlock

from app.application.interfaces.ai_provider_port import (
    AICompletionResult,
    AIProviderError,
    AIProviderPort,
)

DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(AIProviderPort):
    """One instance per configured model -- ``model`` is a constructor
    argument (``Settings.ai_model``, wired in ``app/main.py``'s
    lifespan), not a per-call one, mirroring ``NucleiAdapter``'s binary
    path or ``MinioStoragePort``'s bucket: which model to talk to is
    deployment wiring, not something a caller of ``complete()`` should
    have to decide per request."""

    def __init__(self, *, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def complete(self, *, system_prompt: str, user_prompt: str) -> AICompletionResult:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=DEFAULT_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.APIError as exc:
            # anthropic.APIError is the shared base for every error the
            # SDK raises reaching or getting a valid response from the
            # API -- authentication, rate limits, timeouts, and
            # connection failures all subclass it (confirmed against the
            # installed SDK, not assumed from memory). Translating all of
            # them to one AIProviderError keeps the dependency rule
            # intact: nothing above this adapter ever needs to import
            # anthropic's own exception types.
            raise AIProviderError(f"Anthropic API error: {exc}") from exc

        text_blocks = [block.text for block in response.content if isinstance(block, TextBlock)]
        if not text_blocks:
            raise AIProviderError(
                "Anthropic response contained no text content "
                f"(stop_reason={response.stop_reason!r})"
            )
        return AICompletionResult(text="".join(text_blocks), model=response.model)
