"""Tests for memory_ingest."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.memory_ingest import (
    MemoryIngestError,
    PendingConfirm,
    apply_extracted,
    build_preview_text,
    extract_from_note,
    ingest_note,
    parse_extraction_json,
    strip_json_fence,
    strip_video_grounded_notes,
)

SAMPLE_EXTRACTED = {
    "identity": [],
    "beliefs": [],
    "decisions": [
        {
            "event_title": "Уход с работы",
            "context": "Выгорание на проекте",
            "choice_made": "Ушёл",
            "consequences_lessons": "Стал лучше",
            "event_timestamp": None,
            "is_regret": False,
            "source_quote": "ушёл с работы из-за выгорания",
            "confidence": 0.9,
        }
    ],
    "regrets": [],
    "style": {
        "stop_words": [
            {
                "value": "мега",
                "source_quote": "никогда не говорю мега",
                "confidence": 0.85,
            }
        ],
        "tone_markers": [],
        "syntax": [],
    },
}


def test_strip_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert json.loads(strip_json_fence(raw)) == {"a": 1}


def test_strip_video_grounded_notes_keeps_author_takeaway():
    text = """<!--source:youtube-->
<author_takeaway>
Я понял, что дисциплина важнее мотивации.
</author_takeaway>
<video_grounded_notes>
В видео говорили про 5 утренних привычек миллионеров.
</video_grounded_notes>"""
    stripped = strip_video_grounded_notes(text)
    assert "дисциплина" in stripped
    assert "миллионеров" not in stripped


def test_strip_video_grounded_notes_removes_video_block_without_takeaway():
    text = "<video_grounded_notes>external facts</video_grounded_notes> my note"
    stripped = strip_video_grounded_notes(text)
    assert "external facts" not in stripped
    assert "my note" in stripped


def test_parse_extraction_json_invalid():
    with pytest.raises(MemoryIngestError):
        parse_extraction_json("not json")


def test_build_preview_text_includes_quote():
    raw = "Сегодня ушёл с работы из-за выгорания, никогда не говорю мега"
    preview = build_preview_text(SAMPLE_EXTRACTED, raw)
    assert "Уход с работы" in preview
    assert "выгорания" in preview


def test_apply_extracted_auto_decision(monkeypatch):
    candidates = []

    def _create(**kwargs):
        candidates.append(kwargs)
        return {"id": len(candidates), **kwargs}

    monkeypatch.setattr(
        "src.orchestrator.memory_ingest.create_memory_candidate",
        _create,
    )

    raw = "Сегодня ушёл с работы из-за выгорания, никогда не говорю мега"

    async def _run():
        return await apply_extracted(SAMPLE_EXTRACTED, raw_text=raw, auto=True)

    report = asyncio.run(_run())
    assert report.inserted.get("Review")
    assert [item["candidate_type"] for item in candidates] == ["experience", "style"]
    assert all(item["metadata"]["legacy_ingest_api"] for item in candidates)


def test_apply_extracted_beliefs_pending_without_confirm(monkeypatch):
    extracted = {
        "identity": [],
        "beliefs": [
            {
                "topic": "Деньги",
                "rules_and_values": "Халяль только",
                "source_quote": "только халяль",
                "confidence": 0.95,
            }
        ],
        "decisions": [],
        "regrets": [],
        "style": {"stop_words": [], "tone_markers": [], "syntax": []},
    }
    monkeypatch.setattr(
        "src.orchestrator.memory_ingest.get_supabase", lambda: MagicMock()
    )

    async def _run():
        return await apply_extracted(
            extracted,
            raw_text="только халяль в инвестициях",
            auto=True,
            apply_identity_beliefs=False,
        )

    report = asyncio.run(_run())
    belief_pending = [p for p in report.pending_confirm if p.get("layer") == "beliefs"]
    assert len(belief_pending) == 1


def test_ingest_note_confirm_does_not_apply(monkeypatch):
    monkeypatch.setattr(
        "src.orchestrator.memory_ingest.extract_from_note",
        AsyncMock(return_value=SAMPLE_EXTRACTED),
    )
    monkeypatch.setattr(
        "src.orchestrator.memory_ingest.save_journal_entry",
        lambda **kwargs: 42,
    )

    async def _run():
        return await ingest_note("текст", auto=False)

    result = asyncio.run(_run())
    assert isinstance(result, PendingConfirm)
    assert result.journal_id == 42
    assert "Превью" in result.preview_text


def test_ingest_note_empty_raises():
    async def _run():
        with pytest.raises(MemoryIngestError):
            await ingest_note("   ")

    asyncio.run(_run())


def test_extract_from_note_invalid_json_raises():
    async def _run():
        with patch(
            "src.orchestrator.memory_ingest.generate_text",
            new_callable=AsyncMock,
            return_value="not json",
        ):
            with pytest.raises(MemoryIngestError):
                await extract_from_note("test note")

    asyncio.run(_run())
