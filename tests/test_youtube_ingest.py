"""Tests for YouTube ingest helpers."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from src.orchestrator.youtube_ingest import (
    TranscriptSegment,
    VideoTranscript,
    YouTubeBriefing,
    build_youtube_editor_result,
    compose_editor_input,
    extract_video_id,
    extract_youtube_urls,
    fetch_transcript,
    format_timestamp,
    parse_json3,
    parse_vtt,
    summarize_transcript,
    strip_youtube_urls,
)


def test_extract_youtube_urls_and_strip_comment():
    text = (
        "https://youtu.be/abc123 вот тут понравилась мысль про дисциплину "
        "и https://example.com не должен попасть"
    )
    assert extract_youtube_urls(text) == ["https://youtu.be/abc123"]
    assert strip_youtube_urls(text).startswith("вот тут понравилась")


def test_extract_video_id_from_common_youtube_urls():
    assert extract_video_id("https://youtu.be/abc123?si=x") == "abc123"
    assert extract_video_id("https://www.youtube.com/watch?v=abc123") == "abc123"
    assert extract_video_id("https://youtube.com/shorts/abc123") == "abc123"


def test_parse_vtt_removes_markup_and_duplicates():
    vtt = """WEBVTT

00:00:01.000 --> 00:00:03.000
<c>Привет</c> и полезная мысль

00:00:03.000 --> 00:00:05.000
<c>Привет</c> и полезная мысль

00:00:06.000 --> 00:00:07.500
Новая мысль
"""
    segments = parse_vtt(vtt)
    assert segments == [
        TranscriptSegment(1.0, 3.0, "Привет и полезная мысль"),
        TranscriptSegment(6.0, 7.5, "Новая мысль"),
    ]


def test_parse_json3_segments():
    payload = {
        "events": [
            {
                "tStartMs": 1200,
                "dDurationMs": 800,
                "segs": [{"utf8": "Сначала "}, {"utf8": "факт"}],
            }
        ]
    }
    assert parse_json3(json.dumps(payload)) == [
        TranscriptSegment(1.2, 2.0, "Сначала факт")
    ]


def test_format_timestamp():
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3661) == "01:01:01"


def test_compose_editor_input_separates_author_takeaway_from_video_notes():
    text = compose_editor_input(
        YouTubeBriefing(
            url="https://youtu.be/abc123",
            title="Тестовое видео",
            duration=90,
            transcript_source="auto_captions",
            language="ru",
            briefing="[00:10] Полезная идея из видео.",
            user_takeaway="Мне понравилась практичность.",
        )
    )
    assert "<author_takeaway>" in text
    assert "Мне понравилась практичность." in text
    assert "<video_grounded_notes>" in text
    assert "Полезная идея из видео." in text
    assert "Не сохраняй факты из видео" in text


def test_compose_editor_input_supports_personal_and_source_reference_modes():
    briefing = YouTubeBriefing(
        url="https://youtu.be/abc123",
        title="Тестовое видео",
        duration=90,
        transcript_source="auto_captions",
        language="ru",
        briefing="[00:10] Полезная идея из видео.",
        user_takeaway="Мне понравилась практичность.",
    )

    personal = compose_editor_input(briefing, presentation_mode="personal")
    source = compose_editor_input(briefing, presentation_mode="source_reference")

    assert "<youtube_post_mode>personal</youtube_post_mode>" in personal
    assert "Не упоминай видео" in personal
    assert "Не присваивай мне речь" in personal
    assert "мою личную рефлексию" in personal
    assert "<youtube_post_mode>source_reference</youtube_post_mode>" in source
    assert "обязательно вставь ссылку" in source


def test_fetch_transcript_uses_supadata_when_captions_missing():
    info = {"title": "Test", "duration": 60, "subtitles": {}, "automatic_captions": {}}
    supadata = VideoTranscript(
        url="https://youtu.be/abc123",
        title="Test",
        duration=60,
        source="supadata_transcript",
        language="ru",
        segments=[TranscriptSegment(0.0, 1.0, "hello")],
    )

    async def _run():
        with (
            patch(
                "src.orchestrator.youtube_ingest._extract_info",
                return_value=info,
            ),
            patch(
                "src.orchestrator.youtube_ingest._fetch_caption_transcript",
                AsyncMock(return_value=None),
            ),
            patch(
                "src.orchestrator.youtube_ingest._fetch_supadata_transcript",
                AsyncMock(return_value=supadata),
            ),
            patch(
                "src.orchestrator.youtube_ingest._audio_transcript",
                AsyncMock(side_effect=AssertionError("audio should not run")),
            ),
        ):
            return await fetch_transcript("https://youtu.be/abc123")

    result = asyncio.run(_run())
    assert result.source == "supadata_transcript"


def test_fetch_transcript_uses_supadata_when_metadata_fails():
    supadata = VideoTranscript(
        url="https://youtu.be/abc123",
        title="Test",
        duration=60,
        source="supadata_transcript",
        language="ru",
        segments=[TranscriptSegment(0.0, 1.0, "hello")],
    )

    async def _run():
        with (
            patch(
                "src.orchestrator.youtube_ingest._extract_info",
                side_effect=RuntimeError("blocked"),
            ),
            patch(
                "src.orchestrator.youtube_ingest._fetch_supadata_transcript",
                AsyncMock(return_value=supadata),
            ),
        ):
            return await fetch_transcript("https://youtu.be/abc123")

    result = asyncio.run(_run())
    assert result.source == "supadata_transcript"


def test_fetch_transcript_uses_supadata_when_caption_download_fails():
    info = {
        "title": "Test",
        "duration": 60,
        "subtitles": {"ru": [{"ext": "vtt", "url": "https://captions.test"}]},
        "automatic_captions": {},
    }
    supadata = VideoTranscript(
        url="https://youtu.be/abc123",
        title="Test",
        duration=60,
        source="supadata_transcript",
        language="ru",
        segments=[TranscriptSegment(0.0, 1.0, "hello")],
    )

    async def _run():
        with (
            patch("src.orchestrator.youtube_ingest._extract_info", return_value=info),
            patch(
                "src.orchestrator.youtube_ingest._download_caption",
                AsyncMock(side_effect=RuntimeError("caption expired")),
            ),
            patch(
                "src.orchestrator.youtube_ingest._fetch_supadata_transcript",
                AsyncMock(return_value=supadata),
            ),
            patch(
                "src.orchestrator.youtube_ingest._audio_transcript",
                AsyncMock(side_effect=AssertionError("audio should not run")),
            ),
        ):
            return await fetch_transcript("https://youtu.be/abc123")

    result = asyncio.run(_run())
    assert result.source == "supadata_transcript"


def test_summarize_transcript_processes_all_chunks_without_silent_truncation():
    segments = [
        TranscriptSegment(float(index), float(index + 1), f"полезная мысль {index}")
        for index in range(20)
    ]
    transcript = VideoTranscript(
        url="https://youtu.be/abc123",
        title="Long",
        duration=120,
        source="manual_captions",
        language="ru",
        segments=segments,
    )

    async def _run():
        with (
            patch(
                "src.orchestrator.youtube_ingest._chunk_text",
                return_value=["chunk 1", "chunk 2", "chunk 3"],
            ) as chunk_text,
            patch(
                "src.orchestrator.youtube_ingest._extract_useful_points",
                AsyncMock(side_effect=["[00:01] A", "[00:02] B", "[00:03] C"]),
            ) as extract,
            patch(
                "src.orchestrator.youtube_ingest.generate_text",
                AsyncMock(return_value="merged"),
            ),
        ):
            summary = await summarize_transcript(transcript, user_takeaway="важно")
        return summary, chunk_text, extract

    summary, chunk_text, extract = asyncio.run(_run())
    plain = chunk_text.call_args.args[0]
    assert "полезная мысль 19" in plain
    assert extract.await_count == 3
    assert summary.processed_chunks == 3
    assert summary.total_chunks == 3
    assert summary.coverage == 1.0


def test_build_youtube_editor_result_returns_ingest_metadata():
    transcript = VideoTranscript(
        url="https://youtu.be/abc123",
        title="Meta",
        duration=90,
        source="supadata_transcript",
        language="ru",
        segments=[TranscriptSegment(0.0, 1.0, "hello")],
    )

    async def _run():
        with (
            patch(
                "src.orchestrator.youtube_ingest.fetch_transcript",
                AsyncMock(return_value=transcript),
            ),
            patch(
                "src.orchestrator.youtube_ingest.summarize_transcript",
                AsyncMock(
                    return_value=type(
                        "Summary",
                        (),
                        {
                            "briefing": "[00:01] point",
                            "processed_chunks": 1,
                            "total_chunks": 1,
                            "coverage": 1.0,
                        },
                    )()
                ),
            ),
        ):
            return await build_youtube_editor_result(
                "https://youtu.be/abc123",
                user_takeaway="takeaway",
            )

    result = asyncio.run(_run())
    assert result.transcript_source == "supadata_transcript"
    assert result.processed_chunks == 1
    assert "personal" in result.mode_inputs
    assert "source_reference" in result.mode_inputs
    assert "Покрытие транскрипта: 1/1 chunks" in result.editor_input
