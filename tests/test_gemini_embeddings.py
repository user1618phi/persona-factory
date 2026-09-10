from unittest.mock import MagicMock

from src.embeddings import gemini
from src.embeddings.gemini import _api_keys


def test_embedding_specific_keys_take_priority(monkeypatch):
    monkeypatch.setenv("GEMINI_EMBEDDING_API_KEY", "embedding-primary")
    monkeypatch.setenv("GEMINI_EMBEDDING_API_KEY_FALLBACK", "embedding-fallback")
    monkeypatch.setenv("GEMINI_API_KEY", "generation-primary")
    monkeypatch.setenv("GEMINI_API_KEY_FALLBACK", "generation-fallback")

    assert _api_keys() == [
        "embedding-primary",
        "embedding-fallback",
        "generation-primary",
        "generation-fallback",
    ]


def test_duplicate_embedding_keys_are_removed(monkeypatch):
    monkeypatch.setenv("GEMINI_EMBEDDING_API_KEY", "same-key")
    monkeypatch.delenv("GEMINI_EMBEDDING_API_KEY_FALLBACK", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "same-key")
    monkeypatch.setenv("GEMINI_API_KEY_FALLBACK", "fallback")

    assert _api_keys() == ["same-key", "fallback"]


def test_http_client_is_reused(monkeypatch):
    gemini._http_client.cache_clear()
    gemini._clients.clear()
    constructor = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(gemini.httpx, "Client", constructor)

    first = gemini._http_client(60.0)
    second = gemini._http_client(60.0)

    assert first is second
    constructor.assert_called_once()
