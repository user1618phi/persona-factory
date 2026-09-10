"""Candidate-only learning loop. Nothing becomes identity without review."""

from __future__ import annotations

from typing import Any

from src.db.memory_repository import create_memory_candidate
from src.orchestrator.memory_ingest import extract_from_note, strip_video_grounded_notes


def _confidence(item: dict[str, Any]) -> float:
    try:
        return float(item.get("confidence", 0.5))
    except (TypeError, ValueError):
        return 0.5


def _quote_is_grounded(item: dict[str, Any], text: str) -> bool:
    quote = " ".join(str(item.get("source_quote") or "").split()).lower()
    source = " ".join(text.split()).lower()
    return bool(quote and quote in source)


async def create_candidates_from_text(
    text: str,
    *,
    source_type: str,
    source_id: str | None = None,
    allow_facts: bool = True,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    memory_text = strip_video_grounded_notes(text)
    extracted = await extract_from_note(memory_text)
    created: list[dict[str, Any]] = []
    base_metadata = metadata or {}

    if allow_facts:
        for bucket, candidate_type in (
            ("identity", "identity"),
            ("beliefs", "belief"),
            ("decisions", "experience"),
            ("regrets", "regret"),
        ):
            for item in extracted.get(bucket, []):
                if not _quote_is_grounded(item, memory_text):
                    continue
                row = create_memory_candidate(
                    candidate_type=candidate_type,
                    topic=item.get("topic") or item.get("event_title"),
                    content=item,
                    source_type=source_type,
                    source_id=source_id,
                    source_quote=item.get("source_quote"),
                    confidence=_confidence(item),
                    metadata=base_metadata,
                )
                if row:
                    created.append(row)

    style = extracted.get("style") or {}
    for pattern_type, candidate_type in (
        ("stop_words", "forbidden_phrase"),
        ("tone_markers", "style"),
        ("syntax", "style"),
    ):
        for item in style.get(pattern_type, []):
            if not isinstance(item, dict):
                item = {"value": str(item), "confidence": 0.5}
            if not _quote_is_grounded(item, memory_text):
                continue
            row = create_memory_candidate(
                candidate_type=candidate_type,
                topic=pattern_type,
                content=item,
                source_type=source_type,
                source_id=source_id,
                source_quote=item.get("source_quote"),
                confidence=_confidence(item),
                metadata={**base_metadata, "pattern_type": pattern_type},
            )
            if row:
                created.append(row)
    return created


def create_style_correction_candidate(
    instruction: str,
    *,
    draft_id: str,
) -> dict[str, Any] | None:
    return create_memory_candidate(
        candidate_type="style",
        topic="Корректировка стиля",
        content={"content": instruction, "value": instruction},
        source_type="correction",
        source_id=draft_id,
        source_quote=instruction,
        confidence=1.0,
    )
