"""Gemini embedding helpers via REST API."""

from __future__ import annotations

import atexit
import logging
import os
from functools import lru_cache

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
DEFAULT_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))
_clients: dict[float, httpx.Client] = {}


@lru_cache(maxsize=2)
def _http_client(timeout: float) -> httpx.Client:
    client = httpx.Client(
        timeout=timeout,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    _clients[timeout] = client
    return client


def _close_clients() -> None:
    for client in _clients.values():
        client.close()
    _clients.clear()


atexit.register(_close_clients)


def _api_keys() -> list[str]:
    keys: list[str] = []
    embedding = os.getenv("GEMINI_EMBEDDING_API_KEY", "")
    embedding_fallback = os.getenv("GEMINI_EMBEDDING_API_KEY_FALLBACK", "")
    primary = os.getenv("GEMINI_API_KEY", "")
    fallback = os.getenv("GEMINI_API_KEY_FALLBACK", "")
    for value in (embedding, embedding_fallback, primary, fallback):
        if value and value not in keys:
            keys.append(value)
    if not keys:
        raise RuntimeError(
            "No Gemini embedding key is set (GEMINI_EMBEDDING_API_KEY / GEMINI_API_KEY)"
        )
    return keys


def _embed_with_key(
    api_key: str,
    text: str,
    *,
    model: str,
    dimension: int,
) -> list[float]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent"
    )
    payload = {
        "content": {"parts": [{"text": text}]},
        "outputDimensionality": dimension,
    }

    response = _http_client(60.0).post(
        url,
        headers={
            "Content-Type": "application/json",
            "X-goog-api-key": api_key,
        },
        json=payload,
    )
    response.raise_for_status()

    data = response.json()
    return list(data["embedding"]["values"])


def embed_text(
    text: str, *, model: str | None = None, dimension: int | None = None
) -> list[float]:
    chosen_model = model or DEFAULT_MODEL
    chosen_dimension = dimension or DEFAULT_DIMENSION
    last_error: Exception | None = None

    for index, api_key in enumerate(_api_keys()):
        try:
            return _embed_with_key(
                api_key,
                text,
                model=chosen_model,
                dimension=chosen_dimension,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            slot = f"key-{index + 1}"
            logger.warning("Gemini embed (%s) failed: %s", slot, exc)

    raise RuntimeError(f"Gemini embed failed on all keys: {last_error}")


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    chosen_model = model or DEFAULT_MODEL
    output: list[list[float]] = []
    for start in range(0, len(texts), 100):
        batch = texts[start : start + 100]
        last_error: Exception | None = None
        for index, api_key in enumerate(_api_keys()):
            try:
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{chosen_model}:batchEmbedContents"
                )
                requests = [
                    {
                        "model": f"models/{chosen_model}",
                        "content": {"parts": [{"text": text}]},
                        "outputDimensionality": DEFAULT_DIMENSION,
                    }
                    for text in batch
                ]
                response = _http_client(120.0).post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "X-goog-api-key": api_key,
                    },
                    json={"requests": requests},
                )
                response.raise_for_status()
                embeddings = response.json().get("embeddings") or []
                if len(embeddings) != len(batch):
                    raise RuntimeError(
                        f"Expected {len(batch)} embeddings, received {len(embeddings)}"
                    )
                output.extend([list(item["values"]) for item in embeddings])
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                slot = f"key-{index + 1}"
                logger.warning("Gemini batch embed (%s) failed: %s", slot, exc)
        else:
            raise RuntimeError(f"Gemini batch embed failed on all keys: {last_error}")
    return output
