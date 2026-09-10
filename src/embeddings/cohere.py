"""Cohere embeddings for books RAG pipeline."""

from __future__ import annotations

import atexit
import os
import time
from functools import lru_cache

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = os.getenv("COHERE_EMBEDDING_MODEL", "embed-multilingual-v3.0")
TARGET_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "768"))
RATE_LIMIT_SLEEP = float(os.getenv("COHERE_RATE_LIMIT_SLEEP", "10"))
MAX_RETRIES = int(os.getenv("COHERE_EMBED_MAX_RETRIES", "12"))
API_URL = "https://api.cohere.com/v2/embed"
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


class CohereEmbedRateLimitError(RuntimeError):
    """Embedding API rate limit — stop pipeline and resume later."""


def _api_key() -> str:
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        raise RuntimeError("COHERE_API_KEY is not set")
    return api_key


def _fit_dimension(vector: list[float], *, dimension: int) -> list[float]:
    if len(vector) == dimension:
        return vector
    if len(vector) > dimension:
        return vector[:dimension]
    return vector + [0.0] * (dimension - len(vector))


def _transient_status(status_code: int) -> bool:
    return status_code in {429, 500, 502, 503, 504}


def embed_text(
    text: str,
    *,
    model: str | None = None,
    dimension: int | None = None,
    input_type: str = "search_document",
    max_retries: int | None = None,
) -> list[float]:
    chosen_model = model or DEFAULT_MODEL
    target_dimension = dimension or TARGET_DIMENSION
    retry_limit = max_retries if max_retries is not None else MAX_RETRIES
    payload: dict = {
        "model": chosen_model,
        "texts": [text],
        "input_type": input_type,
        "embedding_types": ["float"],
    }
    if chosen_model.startswith("embed-v4"):
        payload["output_dimension"] = 512 if target_dimension <= 512 else 1024

    last_error: Exception | None = None
    for attempt in range(retry_limit):
        try:
            response = _http_client(90.0).post(
                API_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {_api_key()}",
                },
                json=payload,
            )
            response.raise_for_status()

            data = response.json()
            vectors = data.get("embeddings", {}).get("float", [])
            if not vectors:
                raise RuntimeError(f"Cohere embed returned no vectors: {data}")
            return _fit_dimension(list(vectors[0]), dimension=target_dimension)
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if _transient_status(exc.response.status_code):
                if exc.response.status_code == 429 and attempt + 1 >= retry_limit:
                    raise CohereEmbedRateLimitError(
                        f"Cohere embed rate limit after {retry_limit} retries"
                    ) from exc
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))

    raise RuntimeError(f"Cohere embed failed after {retry_limit} retries: {last_error}")


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    chosen_model = model or DEFAULT_MODEL
    output: list[list[float]] = []
    for start in range(0, len(texts), 96):
        batch = texts[start : start + 96]
        payload: dict = {
            "model": chosen_model,
            "texts": batch,
            "input_type": "search_document",
            "embedding_types": ["float"],
        }
        if chosen_model.startswith("embed-v4"):
            payload["output_dimension"] = 512 if TARGET_DIMENSION <= 512 else 1024
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = _http_client(120.0).post(
                    API_URL,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_api_key()}",
                    },
                    json=payload,
                )
                response.raise_for_status()
                vectors = response.json().get("embeddings", {}).get("float", [])
                if len(vectors) != len(batch):
                    raise RuntimeError(
                        f"Expected {len(batch)} Cohere vectors, received {len(vectors)}"
                    )
                output.extend(
                    [
                        _fit_dimension(list(vector), dimension=TARGET_DIMENSION)
                        for vector in vectors
                    ]
                )
                break
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if not _transient_status(exc.response.status_code):
                    raise
                if exc.response.status_code == 429 and attempt + 1 >= MAX_RETRIES:
                    raise CohereEmbedRateLimitError(
                        f"Cohere batch embed rate limit after {MAX_RETRIES} retries"
                    ) from exc
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
        else:
            raise RuntimeError(f"Cohere batch embed failed: {last_error}")
    return output
