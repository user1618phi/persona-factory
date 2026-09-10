"""Supabase-backed Telegram sessions with an in-process outage fallback."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from src.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)

SESSION_KEYS = (
    "topic",
    "variants",
    "awaiting_correction",
    "mode",
    "journal_id",
    "raw_text",
    "publish_session_id",
    "note_sessions",
    "review_edit",
    "pending_youtube",
)

_fallback_sessions: dict[int, dict[str, Any]] = {}


def _payload(session: dict[str, Any]) -> dict[str, Any]:
    return {key: session[key] for key in SESSION_KEYS if key in session}


def load_session(user_id: int) -> dict[str, Any]:
    try:
        response = (
            get_supabase()
            .table("bot_sessions")
            .select("payload")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        data = rows[0].get("payload") if rows else None
        if isinstance(data, dict):
            _fallback_sessions[user_id] = dict(data)
            return dict(data)
        return dict(_fallback_sessions.get(user_id, {}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session DB read failed; using memory fallback: %s", exc)
        return dict(_fallback_sessions.get(user_id, {}))


def save_session(user_id: int, session: dict[str, Any]) -> None:
    payload = _payload(session)
    _fallback_sessions[user_id] = dict(payload)
    try:
        (
            get_supabase()
            .table("bot_sessions")
            .upsert(
                {
                    "user_id": user_id,
                    "payload": payload,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="user_id",
            )
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session DB write failed; retained in memory: %s", exc)


def clear_session(user_id: int) -> None:
    _fallback_sessions.pop(user_id, None)
    try:
        get_supabase().table("bot_sessions").delete().eq("user_id", user_id).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Session DB delete failed: %s", exc)
