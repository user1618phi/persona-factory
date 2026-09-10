"""Raw text → personality extraction and Supabase ingest."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.config import get_settings
from src.db.memory_repository import create_memory_candidate
from src.db.supabase_client import get_supabase
from src.orchestrator.llm_router import generate_text

SourceType = Literal["note", "channel"]
CONFIDENCE_AUTO_THRESHOLD = 0.75

NOTE_EXTRACTION_PROMPT = """Ты — аналитик цифровой личности. Из ОДНОЙ свежей записи автора извлеки ТОЛЬКО то, что явно следует из его слов.

Верни строго JSON без markdown:
{
  "identity": [{
    "topic": "...",
    "rules_and_values": "...",
    "source_quote": "точная цитата из текста автора",
    "confidence": 0.0
  }],
  "beliefs": [{
    "topic": "...",
    "rules_and_values": "...",
    "source_quote": "точная цитата из текста автора",
    "confidence": 0.0
  }],
  "decisions": [{
    "event_title": "...",
    "context": "...",
    "choice_made": "...",
    "consequences_lessons": "...",
    "event_timestamp": "YYYY-MM-DD или null",
    "is_regret": false,
    "source_quote": "точная цитата из текста автора",
    "confidence": 0.0
  }],
  "regrets": [{
    "event_title": "...",
    "context": "...",
    "choice_made": "...",
    "consequences_lessons": "...",
    "event_timestamp": "YYYY-MM-DD или null",
    "is_regret": true,
    "source_quote": "точная цитата из текста автора",
    "confidence": 0.0
  }],
  "style": {
    "stop_words": [{"value": "...", "source_quote": "...", "confidence": 0.0}],
    "tone_markers": [{"value": "...", "source_quote": "...", "confidence": 0.0}],
    "syntax": [{"value": "...", "source_quote": "...", "confidence": 0.0}]
  }
}

Правила:
- Это свежая запись автора, не выдумывай факты. Если данных нет — пустые массивы.
- Если текст содержит блоки <author_takeaway> и <video_grounded_notes>,
  сведения о личности автора извлекай только из <author_takeaway>.
  <video_grounded_notes> — это факты из внешнего видео,
  не биография и не убеждения автора.
- source_quote — дословная подстрока из текста автора для каждой записи.
- confidence — 0.0–1.0, насколько уверенно извлечение следует из текста.
- identity = долгосрочные цели и миссия.
- beliefs = этика, ислам, финансы, подход к IT.
- decisions = переломные выборы: ситуация → выбор → итог.
- regrets = ошибки, выгорание, упущенные возможности.
- style.stop_words — только слова, которые автор явно избегает.
- Пиши на русском.

ТЕКСТ АВТОРА:
"""

_AUTHOR_TAKEAWAY_RE = re.compile(
    r"<author_takeaway>(.*?)</author_takeaway>",
    re.DOTALL | re.IGNORECASE,
)
_VIDEO_GROUNDED_NOTES_RE = re.compile(
    r"<video_grounded_notes>.*?</video_grounded_notes>",
    re.DOTALL | re.IGNORECASE,
)
_YOUTUBE_SOURCE_MARKER_RE = re.compile(r"<!--source:youtube-->", re.IGNORECASE)


def strip_video_grounded_notes(text: str) -> str:
    """Keep only author-owned text for memory extraction (YouTube-safe)."""
    if not text:
        return text
    takeaway_match = _AUTHOR_TAKEAWAY_RE.search(text)
    if takeaway_match:
        return takeaway_match.group(1).strip()
    cleaned = _VIDEO_GROUNDED_NOTES_RE.sub("", text)
    cleaned = _YOUTUBE_SOURCE_MARKER_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


@dataclass
class IngestReport:
    inserted: dict[str, list[str]] = field(default_factory=dict)
    skipped: dict[str, list[str]] = field(default_factory=dict)
    pending_confirm: list[dict[str, Any]] = field(default_factory=list)
    journal_id: int | None = None
    raw_text: str = ""
    source_type: SourceType = "note"

    def summary_lines(self) -> list[str]:
        lines: list[str] = []
        for layer, items in self.inserted.items():
            for item in items:
                lines.append(f"+ {layer}: {item}")
        for layer, items in self.skipped.items():
            for item in items:
                lines.append(f"Пропущено ({layer}): {item}")
        for item in self.pending_confirm:
            lines.append(
                f"? {item.get('layer')}: «{item.get('topic', item.get('event_title', ''))}» "
                f"— ждёт подтверждения"
            )
        return lines


@dataclass
class PendingConfirm:
    session_id: str
    raw_text: str
    extracted: dict[str, Any]
    journal_id: int
    preview_text: str
    source_type: SourceType = "note"


class MemoryIngestError(ValueError):
    """Invalid input or extraction failure."""


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _quote_in_text(quote: str, raw_text: str) -> bool:
    if not quote or not raw_text:
        return False
    normalized_quote = " ".join(quote.split()).lower()
    normalized_text = " ".join(raw_text.split()).lower()
    return normalized_quote in normalized_text


def _item_confidence(item: dict[str, Any]) -> float:
    try:
        return float(item.get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0


def _normalize_style(style: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    if not style:
        return {"stop_words": [], "tone_markers": [], "syntax": []}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for key in ("stop_words", "tone_markers", "syntax"):
        values = style.get(key, [])
        items: list[dict[str, Any]] = []
        if not values:
            normalized[key] = items
            continue
        for entry in values:
            if isinstance(entry, str):
                items.append({"value": entry, "source_quote": "", "confidence": 0.5})
            elif isinstance(entry, dict):
                items.append(entry)
        normalized[key] = items
    return normalized


def parse_extraction_json(text: str) -> dict[str, Any]:
    cleaned = strip_json_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise MemoryIngestError(f"Невалидный JSON от LLM: {exc}") from exc
    if not isinstance(data, dict):
        raise MemoryIngestError("LLM вернул не объект JSON")
    data.setdefault("identity", [])
    data.setdefault("beliefs", [])
    data.setdefault("decisions", [])
    data.setdefault("regrets", [])
    data["style"] = _normalize_style(data.get("style"))
    return data


async def extract_from_note(text: str) -> dict[str, Any]:
    stripped = strip_video_grounded_notes(text.strip())
    if not stripped:
        raise MemoryIngestError("Пустой текст")
    raw = await generate_text(
        system_prompt="Ты возвращаешь только валидный JSON без markdown.",
        user_prompt=NOTE_EXTRACTION_PROMPT + stripped,
        temperature=0.1,
    )
    return parse_extraction_json(raw)


def _journal_backup_dir() -> Path:
    path = get_settings().project_root / "data" / "journal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_journal_entry(
    *,
    raw_text: str,
    extracted: dict[str, Any] | None,
    applied: bool,
    ingest_report: dict[str, Any] | None,
    source_type: SourceType,
) -> int | None:
    client = get_supabase()
    payload = {
        "raw_text": raw_text,
        "extracted_json": extracted,
        "applied": applied,
        "ingest_report": ingest_report,
        "source_type": source_type,
    }
    response = client.table("journal_entries").insert(payload).execute()
    rows = response.data or []
    journal_id = rows[0]["id"] if rows else None

    if journal_id is not None:
        date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        backup_path = _journal_backup_dir() / f"{date_prefix}_{journal_id}.json"
        with backup_path.open("w", encoding="utf-8") as handle:
            json.dump(
                {
                    "id": journal_id,
                    "raw_text": raw_text,
                    "extracted_json": extracted,
                    "applied": applied,
                    "ingest_report": ingest_report,
                    "source_type": source_type,
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
    return journal_id


def _record_insert(report: IngestReport, layer: str, message: str) -> None:
    report.inserted.setdefault(layer, []).append(message)


def _record_skip(report: IngestReport, layer: str, message: str) -> None:
    report.skipped.setdefault(layer, []).append(message)


def _should_auto_decision(
    item: dict[str, Any], raw_text: str, *, source_type: SourceType
) -> bool:
    quote = item.get("source_quote", "")
    confidence = _item_confidence(item)
    if not _quote_in_text(quote, raw_text):
        return False
    if confidence < CONFIDENCE_AUTO_THRESHOLD:
        return False
    if source_type == "channel":
        context = item.get("context", "")
        return len(context) >= 40 or len(quote) >= 30
    return True


def _should_auto_style_item(
    item: dict[str, Any], raw_text: str, pattern_type: str
) -> bool:
    if pattern_type != "stop_words" and pattern_type != "tone_markers":
        return False
    quote = item.get("source_quote", "")
    if not _quote_in_text(quote, raw_text):
        return False
    return (
        _item_confidence(item) >= CONFIDENCE_AUTO_THRESHOLD
        or pattern_type == "tone_markers"
    )


async def apply_extracted(
    extracted: dict[str, Any],
    *,
    raw_text: str,
    source_type: SourceType = "note",
    auto: bool = True,
    apply_identity_beliefs: bool = False,
    embed_fn: Any = None,
) -> IngestReport:
    """Queue grounded V2.1 review candidates; never write legacy memory tables."""
    del embed_fn  # Kept only for backward-compatible callers.
    report = IngestReport(raw_text=raw_text, source_type=source_type)
    candidate_source = "channel_post" if source_type == "channel" else "note"

    def queue(
        *,
        candidate_type: str,
        topic: str | None,
        content: dict[str, Any],
        source_quote: str,
        confidence: float,
    ) -> None:
        created = create_memory_candidate(
            candidate_type=candidate_type,
            topic=topic,
            content=content,
            source_type=candidate_source,
            source_id=None,
            source_quote=source_quote,
            confidence=confidence,
            metadata={"legacy_ingest_api": True},
        )
        label = (
            topic
            or content.get("event_title")
            or content.get("value")
            or candidate_type
        )
        if created:
            _record_insert(report, "Review", f"{candidate_type}: «{label}»")
        else:
            _record_skip(report, candidate_type, str(label))

    for item in extracted.get("identity", []):
        topic = item.get("topic", "")
        quote = item.get("source_quote", "")
        if not topic:
            continue
        if not apply_identity_beliefs:
            report.pending_confirm.append({**item, "layer": "identity"})
            continue
        queue(
            candidate_type="identity",
            topic=topic,
            content=item,
            source_quote=quote,
            confidence=_item_confidence(item),
        )

    for item in extracted.get("beliefs", []):
        topic = item.get("topic", "")
        quote = item.get("source_quote", "")
        if not topic:
            continue
        if not apply_identity_beliefs:
            report.pending_confirm.append({**item, "layer": "beliefs"})
            continue
        queue(
            candidate_type="belief",
            topic=topic,
            content=item,
            source_quote=quote,
            confidence=_item_confidence(item),
        )

    for bucket, is_regret in (("decisions", False), ("regrets", True)):
        for item in extracted.get(bucket, []):
            title = item.get("event_title", "")
            if not title:
                continue
            if not auto or not _should_auto_decision(
                item, raw_text, source_type=source_type
            ):
                report.pending_confirm.append({**item, "layer": bucket})
                continue
            quote = item.get("source_quote", "")
            queue(
                candidate_type="regret" if is_regret else "experience",
                topic=title,
                content=item,
                source_quote=quote,
                confidence=_item_confidence(item),
            )

    style = extracted.get("style", {})
    for pattern_type in ("stop_words", "tone_markers", "syntax"):
        for entry in style.get(pattern_type, []):
            if not isinstance(entry, dict):
                entry = {"value": str(entry), "source_quote": "", "confidence": 0.0}
            value = entry.get("value", "") if isinstance(entry, dict) else str(entry)
            if not value:
                continue
            if not auto:
                report.pending_confirm.append(
                    {**entry, "layer": f"style.{pattern_type}"}
                )
                continue
            if source_type == "channel" and pattern_type in ("stop_words", "syntax"):
                pass
            elif not _should_auto_style_item(entry, raw_text, pattern_type):
                if source_type == "channel" and pattern_type == "tone_markers":
                    pass
                else:
                    report.pending_confirm.append(
                        {**entry, "layer": f"style.{pattern_type}"}
                    )
                    continue
            queue(
                candidate_type="style",
                topic=pattern_type,
                content={"pattern_type": pattern_type, **entry},
                source_quote=entry.get("source_quote", ""),
                confidence=_item_confidence(entry),
            )

    return report


def build_preview_text(extracted: dict[str, Any], raw_text: str) -> str:
    lines = ["Превью извлечения:", ""]
    for layer in ("identity", "beliefs"):
        for item in extracted.get(layer, []):
            quote = item.get("source_quote", "")
            lines.append(
                f"• {layer}: «{item.get('topic', '')}» "
                f"(confidence {_item_confidence(item):.2f})\n"
                f"  из текста: «{quote[:100]}»"
            )
    for bucket in ("decisions", "regrets"):
        for item in extracted.get(bucket, []):
            quote = item.get("source_quote", "")
            lines.append(
                f"• {bucket}: «{item.get('event_title', '')}» "
                f"(confidence {_item_confidence(item):.2f})\n"
                f"  из текста: «{quote[:100]}»"
            )
    style = extracted.get("style", {})
    for pattern_type in ("stop_words", "tone_markers", "syntax"):
        for entry in style.get(pattern_type, []):
            lines.append(
                f"• style.{pattern_type}: {entry.get('value', '')} "
                f"(confidence {_item_confidence(entry):.2f})"
            )
    if not any(
        extracted.get(k) for k in ("identity", "beliefs", "decisions", "regrets")
    ) and not any(style.get(k) for k in ("stop_words", "tone_markers", "syntax")):
        lines.append("Ничего не извлечено из текста.")
    lines.append("")
    lines.append(f"Исходный текст ({len(raw_text)} симв.):")
    lines.append(raw_text[:500] + ("…" if len(raw_text) > 500 else ""))
    return "\n".join(lines)


async def ingest_note(
    text: str,
    *,
    auto: bool = True,
    source_type: SourceType = "note",
    apply_identity_beliefs: bool | None = None,
) -> IngestReport | PendingConfirm:
    stripped = text.strip()
    if not stripped:
        raise MemoryIngestError("Пустой текст")

    extracted = await extract_from_note(stripped)
    session_id = uuid.uuid4().hex[:12]

    if not auto:
        journal_id = save_journal_entry(
            raw_text=stripped,
            extracted=extracted,
            applied=False,
            ingest_report=None,
            source_type=source_type,
        )
        preview = build_preview_text(extracted, stripped)
        return PendingConfirm(
            session_id=session_id,
            raw_text=stripped,
            extracted=extracted,
            journal_id=journal_id or 0,
            preview_text=preview,
            source_type=source_type,
        )

    apply_beliefs = (
        apply_identity_beliefs if apply_identity_beliefs is not None else False
    )
    report = await apply_extracted(
        extracted,
        raw_text=stripped,
        source_type=source_type,
        auto=True,
        apply_identity_beliefs=apply_beliefs,
    )
    journal_id = save_journal_entry(
        raw_text=stripped,
        extracted=extracted,
        applied=True,
        ingest_report={
            "inserted": report.inserted,
            "skipped": report.skipped,
            "pending_confirm": report.pending_confirm,
        },
        source_type=source_type,
    )
    report.journal_id = journal_id
    return report


async def confirm_and_apply(
    pending: PendingConfirm,
    *,
    apply_identity_beliefs: bool = True,
) -> IngestReport:
    report = await apply_extracted(
        pending.extracted,
        raw_text=pending.raw_text,
        source_type=pending.source_type,
        auto=True,
        apply_identity_beliefs=apply_identity_beliefs,
    )
    client = get_supabase()
    if pending.journal_id:
        client.table("journal_entries").update(
            {
                "applied": True,
                "ingest_report": {
                    "inserted": report.inserted,
                    "skipped": report.skipped,
                    "pending_confirm": report.pending_confirm,
                },
            }
        ).eq("id", pending.journal_id).execute()
    report.journal_id = pending.journal_id
    return report
