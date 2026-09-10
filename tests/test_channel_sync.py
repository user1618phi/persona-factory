"""Tests for channel sync guards and ingest helper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.bot import channel_sync


def test_on_channel_post_skips_when_sync_disabled():
    message = MagicMock()
    message.chat.id = -100
    message.text = "x" * 100
    message.caption = None
    bot = AsyncMock()

    settings = MagicMock()
    settings.channel_sync_enabled = False
    settings.telegram_channel_id = -100

    async def _run():
        with patch("src.bot.channel_sync.get_settings", return_value=settings):
            await channel_sync.on_channel_post(message, bot)
        bot.send_message.assert_not_called()

    asyncio.run(_run())


def test_on_channel_post_notifies_owner_on_failure():
    message = MagicMock()
    message.chat.id = -1001234567890
    message.message_id = 42
    message.text = (
        "Длинный ручной пост канала для синхронизации и извлечения памяти автора. "
        "Здесь достаточно символов для прохождения порога CHANNEL_MIN_POST_CHARS."
    )
    message.caption = None
    message.date = None
    message.chat.title = "Test"
    bot = AsyncMock()

    settings = MagicMock()
    settings.channel_sync_enabled = True
    settings.telegram_channel_id = -1001234567890
    settings.channel_min_post_chars = 80
    settings.my_user_id = 123456789

    async def _run():
        with (
            patch("src.bot.channel_sync.get_settings", return_value=settings),
            patch("src.bot.channel_sync.is_pending_bot_text", return_value=False),
            patch("src.bot.channel_sync.is_bot_published", return_value=False),
            patch("src.bot.channel_sync._already_processed", return_value=False),
            patch(
                "src.bot.channel_sync.ingest_channel_post_text",
                AsyncMock(side_effect=RuntimeError("embed failed")),
            ),
            patch("src.bot.channel_sync.record_channel_sync_error") as record_error,
        ):
            await channel_sync.on_channel_post(message, bot)

        record_error.assert_called_once()
        bot.send_message.assert_called_once()
        assert "Channel sync не удался" in bot.send_message.call_args.args[1]

    asyncio.run(_run())


def test_on_channel_post_records_success():
    message = MagicMock()
    message.chat.id = -1001234567890
    message.message_id = 43
    message.text = (
        "Ещё один достаточно длинный ручной пост для channel sync success path test. "
        "Добавляем текст, чтобы пройти минимальный порог длины для sync handler."
    )
    message.caption = None
    message.date = None
    message.chat.title = "Test"
    bot = AsyncMock()

    settings = MagicMock()
    settings.channel_sync_enabled = True
    settings.telegram_channel_id = -1001234567890
    settings.channel_min_post_chars = 80
    settings.my_user_id = 123456789

    async def _run():
        with (
            patch("src.bot.channel_sync.get_settings", return_value=settings),
            patch("src.bot.channel_sync.is_pending_bot_text", return_value=False),
            patch("src.bot.channel_sync.is_bot_published", return_value=False),
            patch("src.bot.channel_sync._already_processed", return_value=False),
            patch(
                "src.bot.channel_sync.ingest_channel_post_text",
                AsyncMock(
                    return_value={"voice_example_inserted": True, "memory_candidates": 2}
                ),
            ),
            patch("src.bot.channel_sync._record_processed"),
            patch("src.bot.channel_sync.record_channel_sync_success") as record_success,
        ):
            await channel_sync.on_channel_post(message, bot)

        record_success.assert_called_once_with(message_id=43, candidates=2)
        bot.send_message.assert_called_once()

    asyncio.run(_run())
