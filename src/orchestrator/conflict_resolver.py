"""Resolve conflicting belief records."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.db.supabase_client import get_supabase

logger = logging.getLogger(__name__)


def _belief_key(row: dict[str, Any]) -> str:
    return f"{row.get('layer_type', '')}:{row.get('topic', '').strip().lower()}"


def resolve_belief_conflicts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [row for row in records if not row.get("is_deprecated")]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in active:
        grouped[_belief_key(row)].append(row)

    resolved: list[dict[str, Any]] = []
    for items in grouped.values():
        if len(items) == 1:
            resolved.append(items[0])
            continue
        winner = max(items, key=lambda item: item.get("updated_at", ""))
        resolved.append(winner)
    return resolved


def find_belief_conflicts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return conflict groups where multiple active records share the same topic key."""
    active = [row for row in records if not row.get("is_deprecated")]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in active:
        grouped[_belief_key(row)].append(row)

    conflicts: list[dict[str, Any]] = []
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        winner = max(items, key=lambda item: item.get("updated_at", ""))
        losers = [item for item in items if item.get("id") != winner.get("id")]
        conflicts.append(
            {
                "key": key,
                "winner_id": winner.get("id"),
                "loser_ids": [item.get("id") for item in losers],
                "winner_updated_at": winner.get("updated_at"),
                "loser_topics": [item.get("topic") for item in losers],
            }
        )
    return conflicts


def _conflicts_log_path() -> Path:
    path = get_settings().project_root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "conflicts.log"


def _append_conflict_log(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    log_path = _conflicts_log_path()
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conflicts": entries,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


async def deprecate_losers_in_db(records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Mark losing belief records as deprecated when multiple active rows share a topic key.
    Winner = row with latest updated_at. Logs structured JSON to logs/conflicts.log.
    """
    if records is None:
        client = get_supabase()
        response = (
            client.table("core_identity_beliefs")
            .select("*")
            .eq("is_deprecated", False)
            .execute()
        )
        records = response.data or []

    conflicts = find_belief_conflicts(records)
    if not conflicts:
        return {"deprecated_count": 0, "conflicts": []}

    client = get_supabase()
    deprecated_ids: list[int] = []
    for conflict in conflicts:
        for loser_id in conflict["loser_ids"]:
            if loser_id is None:
                continue
            client.table("core_identity_beliefs").update(
                {"is_deprecated": True}
            ).eq("id", loser_id).execute()
            deprecated_ids.append(loser_id)

    _append_conflict_log(conflicts)
    logger.info("Deprecated %s conflicting belief records", len(deprecated_ids))
    return {"deprecated_count": len(deprecated_ids), "conflicts": conflicts}
