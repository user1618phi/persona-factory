"""Learn from manual channel posts without treating them as confirmed facts."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import timezone
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.auth import is_owner as _is_owner
from src.bot.ops_state import record_channel_sync_error, record_channel_sync_success
from src.bot.status_report import format_sync_status
from src.config import get_settings
from src.db.memory_repository import insert_voice_example
from src.db.supabase_client import get_supabase
from src.embeddings.gemini import embed_text
from src.orchestrator.learning import create_candidates_from_text
from src.orchestrator.publisher import is_bot_published, is_pending_bot_text

logger = logging.getLogger(__name__)
router = Router()


def _already_processed(channel_id: int, message_id: int) -> bool:
    response = (
        get_supabase()
        .table("channel_posts_processed")
        .select("id")
        .eq("channel_id", channel_id)
        .eq("message_id", message_id)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def _record_processed(
    *,
    channel_id: int,
    message_id: int,
    text: str,
    post_date,
    report: dict[str, Any],
) -> None:
    get_supabase().table("channel_posts_processed").insert(
        {
            "channel_id": channel_id,
            "message_id": message_id,
            "post_text": text,
            "post_date": post_date.isoformat() if post_date else None,
            "source_type": "channel",
            "ingest_report": report,
        }
    ).execute()


async def ingest_channel_post_text(
    *,
    text: str,
    channel_id: int,
    message_id: int,
    post_date,
    source_chat: str | None,
    source_type: str = "channel_post",
    source_id: str | None = None,
) -> dict[str, Any]:
    """Shared ingest path for live channel posts and published drafts."""
    external_id = source_id or f"{channel_id}:{message_id}"
    embedding = await asyncio.to_thread(embed_text, text)
    inserted = await asyncio.to_thread(
        insert_voice_example,
        text=text,
        source_type=source_type,
        source_external_id=external_id,
        source_chat=source_chat,
        occurred_at=post_date.isoformat() if post_date else None,
        quality_weight=1.0 if source_type == "channel_post" else 0.85,
        embedding=embedding,
        metadata={"channel_id": channel_id, "message_id": message_id},
    )
    candidates = await create_candidates_from_text(
        text,
        source_type=source_type,
        source_id=external_id,
        allow_facts=True,
    )
    return {
        "voice_example_inserted": inserted,
        "memory_candidates": len(candidates),
        "auto_activated": 0,
    }


async def _notify_owner(bot: Bot, text: str) -> None:
    settings = get_settings()
    if not settings.my_user_id:
        return
    try:
        await bot.send_message(settings.my_user_id, text)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to notify owner")


@router.channel_post()
async def on_channel_post(message: Message, bot: Bot) -> None:
    settings = get_settings()
    if (
        not settings.channel_sync_enabled
        or message.chat.id != settings.telegram_channel_id
    ):
        return
    text = (message.text or message.caption or "").strip()
    if not text or len(text) < settings.channel_min_post_chars:
        return
    if is_pending_bot_text(text) or is_bot_published(
        message.chat.id, message.message_id
    ):
        logger.info("Skip bot-published channel post %s", message.message_id)
        return
    if _already_processed(message.chat.id, message.message_id):
        return

    post_date = message.date
    if post_date and post_date.tzinfo is None:
        post_date = post_date.replace(tzinfo=timezone.utc)

    try:
        report = await ingest_channel_post_text(
            text=text,
            channel_id=message.chat.id,
            message_id=message.message_id,
            post_date=post_date,
            source_chat=message.chat.title,
        )
        await asyncio.to_thread(
            _record_processed,
            channel_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            post_date=post_date,
            report=report,
        )
        record_channel_sync_success(
            message_id=message.message_id,
            candidates=int(report["memory_candidates"]),
        )
        await _notify_owner(
            bot,
            f"Ручной пост {message.message_id} добавлен как пример голоса. "
            f"Кандидатов на подтверждение: {report['memory_candidates']}. /review",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Channel sync failed for post %s", message.message_id)
        record_channel_sync_error(message_id=message.message_id, error=str(exc))
        await _notify_owner(
            bot,
            "Channel sync не удался для поста "
            f"{message.message_id}: {html.escape(str(exc))}",
        )


@router.message(Command("sync"))
async def cmd_sync_status(message: Message, command: CommandObject) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    if (command.args or "status").strip().lower() != "status":
        await message.answer("Доступно: /sync status")
        return
    await message.answer(format_sync_status())
