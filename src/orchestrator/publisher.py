"""Idempotent Telegram channel publishing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import Bot

from src.bot.ops_state import record_publish_memory_candidates
from src.config import get_settings
from src.db.memory_repository import (
    get_draft,
    insert_voice_example,
    latest_draft_version,
    update_draft,
)
from src.db.supabase_client import get_supabase
from src.embeddings.gemini import embed_text

from src.orchestrator.learning import create_candidates_from_text

logger = logging.getLogger(__name__)

_pending_texts: set[str] = set()


class PublishError(RuntimeError):
    """Channel publish failed or not configured."""


def is_bot_published(channel_id: int, message_id: int) -> bool:
    response = (
        get_supabase()
        .table("bot_published_posts")
        .select("id")
        .eq("channel_id", channel_id)
        .eq("message_id", message_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def is_pending_bot_text(text: str) -> bool:
    normalized = text.strip()
    if normalized in _pending_texts:
        return True
    try:
        response = (
            get_supabase()
            .table("drafts")
            .select("id")
            .eq("state", "publishing")
            .eq("final_text", normalized)
            .limit(1)
            .execute()
        )
        return bool(response.data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Persistent publish guard unavailable: %s", exc)
        return False


def _published_by_token(publish_token: str) -> dict[str, Any] | None:
    response = (
        get_supabase()
        .table("bot_published_posts")
        .select("*")
        .eq("publish_token", publish_token)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


async def publish_draft(bot: Bot, draft_id: str) -> tuple[int, bool]:
    draft = get_draft(draft_id)
    if not draft:
        raise PublishError("Черновик не найден.")
    if draft.get("state") == "publishing":
        raise PublishError(
            "Публикация уже выполняется. Повторная отправка заблокирована."
        )
    if draft.get("state") not in {"preview", "published"}:
        raise PublishError("Сначала откройте финальный preview.")

    publish_token = str(draft["publish_token"])
    existing = _published_by_token(publish_token)
    if existing:
        return int(existing["message_id"]), False

    text = (draft.get("final_text") or "").strip()
    if not text:
        raise PublishError("Финальный текст пуст.")
    settings = get_settings()
    if not settings.telegram_channel_id:
        raise PublishError("TELEGRAM_CHANNEL_ID не задан.")

    update_draft(draft_id, state="publishing")
    _pending_texts.add(text)
    try:
        message = await bot.send_message(settings.telegram_channel_id, text)
    except Exception:
        _pending_texts.discard(text)
        update_draft(draft_id, state="preview")
        raise
    version = latest_draft_version(draft_id)
    payload = {
        "channel_id": settings.telegram_channel_id,
        "message_id": message.message_id,
        "raw_input_id": draft.get("journal_id"),
        "journal_id": draft.get("journal_id"),
        "draft_id": draft_id,
        "publish_token": publish_token,
        "final_version_id": version.get("id") if version else None,
        "published_text": text,
    }
    try:
        get_supabase().table("bot_published_posts").insert(payload).execute()
    except Exception:
        # Telegram send can succeed while DB insert races/fails. Re-check the token
        # before reporting a failure so a repeated click never publishes twice.
        existing = _published_by_token(publish_token)
        if not existing:
            update_draft(draft_id, state="failed")
            raise PublishError(
                "Пост отправлен, но запись публикации не сохранилась. "
                "Повторная отправка заблокирована; требуется ручная проверка канала."
            )
        return int(existing["message_id"]), False

    update_draft(
        draft_id,
        state="published",
        published_message_id=message.message_id,
    )
    if draft.get("journal_id"):
        get_supabase().table("journal_entries").update(
            {"applied": True, "processing_state": "published"}
        ).eq("id", draft["journal_id"]).execute()
    try:
        vector = await asyncio.to_thread(embed_text, text)
        await asyncio.to_thread(
            insert_voice_example,
            text=text,
            source_type="accepted_draft",
            source_external_id=draft_id,
            source_chat="persona_factory",
            occurred_at=None,
            quality_weight=0.4,
            embedding=vector,
            metadata={
                "human_approved": True,
                "published_message_id": message.message_id,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Published draft was not added to voice_examples")
    try:
        candidates = await create_candidates_from_text(
            text,
            source_type="channel_post",
            source_id=f"published:{draft_id}",
            allow_facts=True,
            metadata={"origin": "published_draft"},
        )
        record_publish_memory_candidates(
            draft_id=draft_id,
            message_id=message.message_id,
            candidates=len(candidates),
        )
        if candidates and settings.my_user_id:
            await bot.send_message(
                settings.my_user_id,
                f"Опубликованный пост: создано {len(candidates)} кандидатов для /review.",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Published draft memory candidates were not created")
    _pending_texts.discard(text)
    return message.message_id, True


async def send_to_channel(
    bot: Bot,
    text: str,
    *,
    raw_input_id: int | None = None,
) -> int:
    """Legacy compatibility for callers outside the V2.1 draft workflow."""
    settings = get_settings()
    if not settings.telegram_channel_id:
        raise PublishError("TELEGRAM_CHANNEL_ID не задан в .env")
    message = await bot.send_message(settings.telegram_channel_id, text)
    get_supabase().table("bot_published_posts").insert(
        {
            "channel_id": settings.telegram_channel_id,
            "message_id": message.message_id,
            "published_text": text,
            "raw_input_id": raw_input_id,
            "journal_id": raw_input_id,
        }
    ).execute()
    return message.message_id
