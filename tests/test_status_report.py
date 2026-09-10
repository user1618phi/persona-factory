"""Tests for production status report formatting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.bot.status_report import collect_status_counts, format_production_status, format_sync_status


def test_format_production_status_includes_knowledge_and_sync():
    settings = MagicMock()
    settings.gemini_api_key = "x"
    settings.gemini_api_key_fallback = "x"
    settings.groq_api_key = "x"
    settings.cohere_api_key = "x"
    settings.cohere_api_key_fallback = ""
    settings.openrouter_api_key = ""
    settings.supadata_api_key = "x"
    settings.telegram_channel_id = 123
    settings.channel_sync_enabled = True

    counts = {
        "persona_rules": 1,
        "voice_examples": 2,
        "life_experiences": 3,
        "memory_candidates": 4,
        "pending_review": 0,
        "verified_knowledge": 5,
        "unverified_knowledge": 10,
        "islam_unverified": 2,
        "channel_posts_processed": 3,
        "channel_voice_examples": 4,
    }

    with (
        patch("src.bot.status_report.collect_status_counts", return_value=counts),
        patch("src.bot.status_report.ops_snapshot", return_value={}),
    ):
        text = format_production_status(settings)

    assert "knowledge verified: 5" in text
    assert "Channel sync: ON" in text
    assert "Supadata (YouTube): OK" in text


def test_format_sync_status_warns_when_disabled():
    settings = MagicMock()
    settings.channel_sync_enabled = False
    settings.telegram_channel_id = 123
    settings.channel_min_post_chars = 80

    with (
        patch(
            "src.bot.status_report.collect_status_counts",
            return_value={"channel_posts_processed": 0, "channel_voice_examples": 0},
        ),
        patch("src.bot.status_report.ops_snapshot", return_value={}),
    ):
        text = format_sync_status(settings)

    assert "OFF" in text
    assert "CHANNEL_SYNC_ENABLED=true" in text


def test_collect_status_counts_degrades_when_one_query_fails():
    def count(table, *, filters=None):
        if table == "knowledge_chunks" and filters == {"verification_status": "verified"}:
            raise RuntimeError("db unavailable")
        return 1

    with (
        patch("src.bot.status_report._count_table", side_effect=count),
        patch("src.bot.status_report.record_status_error") as record_error,
    ):
        counts = collect_status_counts()

    assert counts["verified_knowledge"] is None
    assert counts["persona_rules"] == 1
    record_error.assert_called_once()
