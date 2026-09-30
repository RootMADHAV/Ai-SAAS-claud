"""Unit tests for
app.infrastructure.embeddings.sentence_transformer_provider.SentenceTransformerEmbeddingPort.

No real ``sentence-transformers`` model is ever loaded here --
``SentenceTransformer`` is patched at construction time with a fake that
returns a canned vector, rather than downloading/loading a real model.
Mirrors test_anthropic_provider.py's/test_minio_storage.py's own pattern
for wrapping an official SDK/library: this suite verifies
``SentenceTransformerEmbeddingPort``'s own logic (constructor wiring,
result shape, error translation), not the model's actual output, the
same verification-tier distinction already documented for
``MinioStoragePort``/``AnthropicProvider``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.application.interfaces.embedding_port import EmbeddingError
from app.infrastructure.embeddings.sentence_transformer_provider import (
    DEFAULT_MODEL_NAME,
    SentenceTransformerEmbeddingPort,
)


@dataclass
class _FakeModel:
    vector: list[object] | None = None
    error: Exception | None = None
    encode_calls: list[str] = field(default_factory=list)

    def encode(self, text: str) -> list[object]:
        self.encode_calls.append(text)
        if self.error is not None:
            raise self.error
        assert self.vector is not None
        return self.vector


def _install_fake_model(
    monkeypatch: pytest.MonkeyPatch, fake: _FakeModel
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Patches the module's imported ``SentenceTransformer`` name with a
    fake constructor that always returns ``fake``, and returns the list
    of (args, kwargs) it was constructed with -- mirrors
    test_anthropic_provider.py's ``_install_fake_client`` shape."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _fake_constructor(*args: object, **kwargs: object) -> _FakeModel:
        calls.append((args, kwargs))
        return fake

    monkeypatch.setattr(
        "app.infrastructure.embeddings.sentence_transformer_provider.SentenceTransformer",
        _fake_constructor,
    )
    return calls


async def test_model_name_defaults_to_all_minilm_l6_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, _FakeModel())
    port = SentenceTransformerEmbeddingPort()
    assert port.model_name == DEFAULT_MODEL_NAME == "all-MiniLM-L6-v2"


async def test_model_name_uses_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_model(monkeypatch, _FakeModel())
    port = SentenceTransformerEmbeddingPort(model_name="a-different-model")
    assert port.model_name == "a-different-model"


async def test_constructor_loads_the_model_on_cpu_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_model(monkeypatch, _FakeModel())

    SentenceTransformerEmbeddingPort(model_name="all-MiniLM-L6-v2")

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("all-MiniLM-L6-v2",)
    assert kwargs == {"device": "cpu"}


async def test_embed_returns_vector_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeModel(vector=[0.1, 0.2, 0.3])
    _install_fake_model(monkeypatch, fake)
    port = SentenceTransformerEmbeddingPort()

    result = await port.embed("some finding text")

    assert result.vector == (0.1, 0.2, 0.3)
    assert result.model == DEFAULT_MODEL_NAME


async def test_embed_converts_non_native_numeric_values_to_plain_floats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real ``encode()`` returns a ``numpy.ndarray`` of
    ``numpy.float32`` values, not native Python ``float``s. A minimal
    stand-in with the same "supports ``float(x)``" shape -- not numpy
    itself -- is enough to verify ``embed()``'s conversion actually
    narrows every element to a plain ``float``, not just numbers that
    already happen to be one."""

    class _NumericLike:
        def __init__(self, value: float) -> None:
            self._value = value

        def __float__(self) -> float:
            return self._value

    fake = _FakeModel(vector=[_NumericLike(0.5), _NumericLike(-0.25)])
    _install_fake_model(monkeypatch, fake)
    port = SentenceTransformerEmbeddingPort()

    result = await port.embed("text")

    assert result.vector == (0.5, -0.25)
    assert all(type(x) is float for x in result.vector)


async def test_embed_passes_text_through_to_encode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeModel(vector=[0.0])
    _install_fake_model(monkeypatch, fake)
    port = SentenceTransformerEmbeddingPort()

    await port.embed("analyze this finding")

    assert fake.encode_calls == ["analyze this finding"]


async def test_embed_raises_embedding_error_on_underlying_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel(error=RuntimeError("model blew up"))
    _install_fake_model(monkeypatch, fake)
    port = SentenceTransformerEmbeddingPort()

    with pytest.raises(EmbeddingError, match="failed to embed text"):
        await port.embed("text")


async def test_embed_does_not_swallow_the_original_exception_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeModel(error=ValueError("bad input shape"))
    _install_fake_model(monkeypatch, fake)
    port = SentenceTransformerEmbeddingPort()

    with pytest.raises(EmbeddingError, match="bad input shape"):
        await port.embed("text")
