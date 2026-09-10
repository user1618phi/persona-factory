"""Shared production status lines for /status and /sync."""

from __future__ import annotations

import logging
from typing import Any

from src.bot.ops_state import record_status_error, snapshot as ops_snapshot
from src.config import Settings, get_settings
from src.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def _count_table(
    table: str,
    *,
    filters: dict[str, Any] | None = None,
) -> int:
    query = get_supabase().table(table).select("id", count="exact")
    if filters:
        for key, value in filters.items():
            query = query.eq(key, value)
    response = query.limit(1).execute()
    return int(response.count or 0)


def _safe_count(label: str, table: str, **filters: Any) -> int | None:
    try:
        return _count_table(table, filters=filters or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Status count failed for %s: %s", label, exc)
        record_status_error(label, str(exc))
        return None


def _sum_counts(*values: int | None) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(int(value or 0) for value in values)


def collect_status_counts() -> dict[str, int | None]:
    pending_candidates = _safe_count(
        "pending memory_candidates", "memory_candidates", status="pending"
    )
    pending_rules = _safe_count("pending persona_rules", "persona_rules", status="pending")
    pending_experiences = _safe_count(
        "pending life_experiences", "life_experiences", status="pending"
    )
    return {
        "persona_rules": _safe_count("persona_rules", "persona_rules"),
        "voice_examples": _safe_count("voice_examples", "voice_examples"),
        "life_experiences": _safe_count("life_experiences", "life_experiences"),
        "memory_candidates": _safe_count("memory_candidates", "memory_candidates"),
        "pending_review": _sum_counts(
            pending_candidates,
            pending_rules,
            pending_experiences,
        ),
        "verified_knowledge": _safe_count(
            "verified knowledge", "knowledge_chunks", verification_status="verified"
        ),
        "unverified_knowledge": _safe_count(
            "unverified knowledge",
            "knowledge_chunks",
            verification_status="unverified",
        ),
        "islam_unverified": _safe_count(
            "islam unverified",
            "knowledge_chunks",
            category="islam",
            verification_status="unverified",
        ),
        "channel_posts_processed": _safe_count(
            "channel posts processed", "channel_posts_processed"
        ),
        "channel_voice_examples": _safe_count(
            "channel voice examples", "voice_examples", source_type="channel_post"
        ),
    }


def _fmt(value: int | None) -> str:
    return str(value) if value is not None else "n/a"


def format_production_status(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    counts = collect_status_counts()
    ops = ops_snapshot(include_persistent=True)
    last_error = ops.get("channel_sync_last_error")
    last_success = ops.get("channel_sync_last_success")
    youtube_success = ops.get("youtube_last_success")
    youtube_error = ops.get("youtube_last_error")

    lines = [
        "Production status:",
        f"LLM Gemini primary: {'OK' if settings.gemini_api_key else 'MISSING'}",
        f"LLM Gemini fallback: {'OK' if settings.gemini_api_key_fallback else 'MISSING'}",
        f"LLM Groq: {'OK' if settings.groq_api_key else 'MISSING'}",
        f"LLM Cohere primary: {'OK' if settings.cohere_api_key else 'MISSING'}",
        f"LLM Cohere fallback: {'OK' if settings.cohere_api_key_fallback else 'MISSING'}",
        f"LLM OpenRouter: {'OK' if settings.openrouter_api_key else 'MISSING'}",
        f"Supadata (YouTube): {'OK' if settings.supadata_api_key else 'MISSING'}",
        f"Channel ID: {'OK' if settings.telegram_channel_id else 'MISSING'}",
        f"Channel sync: {'ON' if settings.channel_sync_enabled else 'OFF'}",
        f"persona_rules: {_fmt(counts['persona_rules'])}",
        f"voice_examples: {_fmt(counts['voice_examples'])}",
        f"life_experiences: {_fmt(counts['life_experiences'])}",
        f"memory_candidates: {_fmt(counts['memory_candidates'])}",
        f"pending_review: {_fmt(counts['pending_review'])}",
        f"knowledge verified: {_fmt(counts['verified_knowledge'])}",
        f"knowledge unverified: {_fmt(counts['unverified_knowledge'])}",
        f"islam unverified: {_fmt(counts['islam_unverified'])}",
        f"channel sync processed: {_fmt(counts['channel_posts_processed'])}",
    ]
    if last_success:
        lines.append(
            "last channel sync OK: post "
            f"{last_success.get('message_id')} "
            f"({last_success.get('candidates', 0)} candidates) "
            f"at {last_success.get('at')}"
        )
    if last_error:
        lines.append(
            "last channel sync ERROR: post "
            f"{last_error.get('message_id')}: {last_error.get('error')} "
            f"at {last_error.get('at')}"
        )
    if youtube_success:
        lines.append(
            "last YouTube OK: "
            f"{youtube_success.get('source')} "
            f"chunks {youtube_success.get('processed_chunks')}/"
            f"{youtube_success.get('total_chunks')} "
            f"coverage {youtube_success.get('coverage')} "
            f"at {youtube_success.get('at')}"
        )
    if youtube_error:
        lines.append(
            f"last YouTube ERROR: {youtube_error.get('error')} "
            f"at {youtube_error.get('at')}"
        )
    return "\n".join(lines)


def format_sync_status(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    counts = collect_status_counts()
    ops = ops_snapshot(include_persistent=True)
    last_error = ops.get("channel_sync_last_error")
    lines = [
        f"Channel Sync: {'ON' if settings.channel_sync_enabled else 'OFF'}",
        f"Канал: {settings.telegram_channel_id}",
        f"Min post chars: {settings.channel_min_post_chars}",
        f"Обработано ручных постов: {_fmt(counts['channel_posts_processed'])}",
        f"Channel voice examples: {_fmt(counts['channel_voice_examples'])}",
    ]
    if not settings.channel_sync_enabled:
        lines.append(
            "Подсказка: включите CHANNEL_SYNC_ENABLED=true и добавьте бота админом канала."
        )
    if last_error:
        lines.append(
            f"Последняя ошибка sync: post {last_error.get('message_id')} — "
            f"{last_error.get('error')}"
        )
    return "\n".join(lines)
