"""Tests for in-process ops telemetry."""

from __future__ import annotations

from unittest.mock import patch

from src.bot import ops_state


def test_record_channel_sync_success_clears_error():
    with patch("src.bot.ops_state._record_event"):
        ops_state.record_channel_sync_error(message_id=1, error="boom")
        ops_state.record_channel_sync_success(message_id=2, candidates=3)
    snap = ops_state.snapshot()
    assert snap["channel_sync_last_error"] is None
    assert snap["channel_sync_last_success"]["message_id"] == 2
    assert snap["channel_sync_last_success"]["candidates"] == 3


def test_record_youtube_error():
    with patch("src.bot.ops_state._record_event"):
        ops_state.record_youtube_error("timeout")
    snap = ops_state.snapshot()
    assert "timeout" in snap["youtube_last_error"]["error"]


def test_record_youtube_success_persists_best_effort_event():
    with patch("src.bot.ops_state._record_event") as record:
        ops_state.record_youtube_success(
            url="https://youtu.be/abc123",
            source="supadata_transcript",
            duration=90,
            processed_chunks=2,
            total_chunks=2,
            coverage=1.0,
        )

    record.assert_called_once()
    assert record.call_args.args[0] == "youtube_success"
    assert record.call_args.kwargs["metadata"]["source"] == "supadata_transcript"
    snap = ops_state.snapshot()
    assert snap["youtube_last_success"]["processed_chunks"] == 2


def test_snapshot_can_read_persistent_events():
    row = {
        "event_type": "youtube_success",
        "status": "success",
        "message_id": None,
        "source_id": "https://youtu.be/abc123",
        "summary": "supadata_transcript; chunks 3/3",
        "created_at": "2026-06-27T10:00:00+00:00",
        "metadata": {
            "source": "supadata_transcript",
            "processed_chunks": 3,
            "total_chunks": 3,
            "coverage": 1.0,
        },
    }
    with patch("src.bot.ops_state._latest_event") as latest:
        latest.side_effect = lambda event_type: row if event_type == "youtube_success" else None
        snap = ops_state.snapshot(include_persistent=True)

    assert snap["youtube_last_success"]["source"] == "supadata_transcript"
    assert snap["youtube_last_success"]["processed_chunks"] == 3
