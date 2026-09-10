"""Cohere REST client for books pipeline (profile extraction)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = os.getenv("COHERE_BOOKS_MODEL", "command-r-plus-08-2024")
RATE_LIMIT_SLEEP = float(os.getenv("COHERE_RATE_LIMIT_SLEEP", "10"))
API_URL = "https://api.cohere.com/v2/chat"


class CohereError(RuntimeError):
    pass


class CohereRateLimitError(CohereError):
    """API rate/quota limit exhausted — safe to stop and resume later."""


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _api_key() -> str:
    api_key = os.getenv("COHERE_API_KEY")
    if not api_key:
        raise CohereError("COHERE_API_KEY is not set in .env")
    return api_key


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate" in message or "quota" in message


def _is_transient_http(status_code: int) -> bool:
    return status_code in {429, 500, 502, 503, 504}


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.2,
    max_retries: int = 12,
    timeout: float = 180.0,
) -> dict[str, Any]:
    chosen_model = model or DEFAULT_MODEL
    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    API_URL,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_api_key()}",
                    },
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            message = data.get("message", {})
            content_parts = message.get("content", [])
            if not content_parts:
                raise CohereError(f"Cohere returned empty content: {data}")

            text = content_parts[0].get("text", "")
            if not text.strip():
                raise CohereError(f"Cohere returned empty text: {data}")

            return json.loads(strip_json_fence(text))
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status = exc.response.status_code
            if status == 429:
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            elif _is_transient_http(status):
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                raise CohereError(f"Cohere HTTP error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_rate_limit_error(exc):
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                time.sleep(3 * (attempt + 1))

    if last_error and "429" in str(last_error):
        raise CohereRateLimitError(
            f"Cohere rate limit after {max_retries} retries: {last_error}"
        ) from last_error
    raise CohereError(f"Cohere call failed after {max_retries} retries: {last_error}")
