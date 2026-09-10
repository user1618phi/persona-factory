"""Operational telemetry for owner-facing /status."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from threading import Lock
from typing import Any

from src.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)

_lock = Lock()
_state: dict[str, Any] = {
    "channel_sync_last_error": None,
    "channel_sync_last_success": None,
    "youtube_last_success": None,
    "youtube_last_error": None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_event(
    event_type: str,
    *,
    status: str,
    message_id: int | None = None,
    source_id: str | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a best-effort ops event without breaking the caller."""
    payload = {
        "event_type": event_type,
        "status": status,
        "message_id": message_id,
        "source_id": source_id,
        "summary": (summary or "")[:1000],
        "metadata": metadata or {},
    }
    try:
        get_supabase().table("bot_ops_events").insert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not persist bot ops event %s: %s", event_type, exc)


def _latest_event(event_type: str) -> dict[str, Any] | None:
    try:
        response = (
            get_supabase()
            .table("bot_ops_events")
            .select("*")
            .eq("event_type", event_type)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read bot ops event %s: %s", event_type, exc)
        return None
    rows = response.data or []
    return rows[0] if rows else None


def _event_to_status(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    status = {
        "at": row.get("created_at"),
        "message_id": row.get("message_id"),
        "source_id": row.get("source_id"),
        "summary": row.get("summary"),
    }
    status.update(metadata)
    if row.get("status") == "error":
        status["error"] = row.get("summary")
    return status


def record_channel_sync_success(*, message_id: int, candidates: int) -> None:
    event = {
        "at": _now_iso(),
        "message_id": message_id,
        "candidates": candidates,
    }
    with _lock:
        _state["channel_sync_last_success"] = event
        _state["channel_sync_last_error"] = None
    _record_event(
        "channel_sync_success",
        status="success",
        message_id=message_id,
        summary=f"channel post synced with {candidates} candidate(s)",
        metadata={"candidates": candidates},
    )


def record_channel_sync_error(*, message_id: int, error: str) -> None:
    event = {
        "at": _now_iso(),
        "message_id": message_id,
        "error": error[:500],
    }
    with _lock:
        _state["channel_sync_last_error"] = event
    _record_event(
        "channel_sync_error",
        status="error",
        message_id=message_id,
        summary=error,
    )


def record_youtube_success(
    *,
    url: str,
    source: str,
    duration: int | None,
    processed_chunks: int,
    total_chunks: int,
    coverage: float,
) -> None:
    event = {
        "at": _now_iso(),
        "url": url,
        "source": source,
        "duration": duration,
        "processed_chunks": processed_chunks,
        "total_chunks": total_chunks,
        "coverage": coverage,
    }
    with _lock:
        _state["youtube_last_success"] = event
        _state["youtube_last_error"] = None
    _record_event(
        "youtube_success",
        status="success",
        source_id=url,
        summary=f"{source}; chunks {processed_chunks}/{total_chunks}",
        metadata={
            "url": url,
            "source": source,
            "duration": duration,
            "processed_chunks": processed_chunks,
            "total_chunks": total_chunks,
            "coverage": coverage,
        },
    )


def record_youtube_error(error: str) -> None:
    event = {
        "at": _now_iso(),
        "error": error[:500],
    }
    with _lock:
        _state["youtube_last_error"] = event
    _record_event("youtube_error", status="error", summary=error)


def record_publish_memory_candidates(
    *,
    draft_id: str,
    message_id: int,
    candidates: int,
) -> None:
    _record_event(
        "publish_memory_candidates",
        status="info",
        message_id=message_id,
        source_id=f"published:{draft_id}",
        summary=f"published draft created {candidates} memory candidate(s)",
        metadata={"draft_id": draft_id, "candidates": candidates},
    )


def record_status_error(label: str, error: str) -> None:
    _record_event(
        "status_error",
        status="error",
        summary=f"{label}: {error}",
        metadata={"label": label},
    )


def snapshot(*, include_persistent: bool = False) -> dict[str, Any]:
    with _lock:
        current = dict(_state)
    if not include_persistent:
        return current
    for key, event_type in (
        ("channel_sync_last_success", "channel_sync_success"),
        ("channel_sync_last_error", "channel_sync_error"),
        ("youtube_last_success", "youtube_success"),
        ("youtube_last_error", "youtube_error"),
    ):
        row = _latest_event(event_type)
        if row:
            current[key] = _event_to_status(row)
    return current
