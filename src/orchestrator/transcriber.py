"""Voice transcription using Groq Whisper API."""

from __future__ import annotations

import logging

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str = "voice.ogg",
    content_type: str = "audio/ogg",
    timeout_seconds: float = 30.0,
    language: str = "ru",
) -> str:
    """Transcribes audio bytes using Groq's whisper model."""
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set.")

    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    files = {"file": (filename, audio_bytes, content_type)}
    data = {
        "model": "whisper-large-v3-turbo",
        "response_format": "json",
        "language": language,
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=headers, data=data, files=files, timeout=timeout_seconds
        )
        response.raise_for_status()
        return response.json().get("text", "").strip()
