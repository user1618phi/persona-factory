"""Shared Gemini REST client with retries and rate-limit handling."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
RATE_LIMIT_SLEEP = float(os.getenv("GEMINI_RATE_LIMIT_SLEEP", "15"))


class GeminiError(RuntimeError):
    pass


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate" in message or "quota" in message or "503" in message


def generate_json(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.2,
    max_retries: int = 5,
    timeout: float = 180.0,
) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY is not set in .env")

    chosen_model = model or DEFAULT_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{chosen_model}:generateContent"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "X-goog-api-key": api_key,
                    },
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise GeminiError(f"Gemini returned no candidates: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise GeminiError(f"Gemini returned empty content: {data}")

            text = parts[0].get("text", "")
            return json.loads(strip_json_fence(text))
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in {429, 503}:
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                raise GeminiError(f"Gemini HTTP error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_rate_limit_error(exc):
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                time.sleep(3 * (attempt + 1))

    raise GeminiError(f"Gemini call failed after {max_retries} retries: {last_error}")


def generate_text(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_retries: int = 5,
    timeout: float = 180.0,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiError("GEMINI_API_KEY is not set in .env")

    chosen_model = model or DEFAULT_MODEL
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{chosen_model}:generateContent"
    )
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": temperature},
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "X-goog-api-key": api_key,
                    },
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise GeminiError(f"Gemini returned no candidates: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            text = parts[0].get("text", "") if parts else ""
            if not text.strip():
                raise GeminiError("Gemini returned empty text")
            return text.strip()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            if exc.response.status_code in {429, 503}:
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                raise GeminiError(f"Gemini HTTP error: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_rate_limit_error(exc):
                time.sleep(RATE_LIMIT_SLEEP * (attempt + 1))
            else:
                time.sleep(3 * (attempt + 1))

    raise GeminiError(f"Gemini text call failed after {max_retries} retries: {last_error}")
