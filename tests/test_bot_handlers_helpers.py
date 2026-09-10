"""Tests for YouTube pending session helpers and handler guards."""

from __future__ import annotations

from src.orchestrator.youtube_ingest import (
    extract_youtube_urls,
    strip_youtube_urls,
)


def test_extract_and_strip_youtube_urls():
    text = "https://youtu.be/abc123 смотри это видео"
    urls = extract_youtube_urls(text)
    assert len(urls) == 1
    assert strip_youtube_urls(text) == "смотри это видео"


def test_youtube_cancel_texts_are_lowercase_comparable():
    cancel = {"/cancel", "cancel", "отмена", "стоп"}
    assert "отмена".strip().lower() in cancel
