"""Legacy schema seed utility.

Production runtime must use ``memory_repository`` and the V2.1 review queue.
This module remains only for one-time legacy imports and rollback tooling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.db.supabase_client import get_supabase
from src.embeddings.gemini import embed_text


def parse_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


def belief_exists(client, *, layer_type: str, topic: str) -> bool:
    response = (
        client.table("core_identity_beliefs")
        .select("id")
        .eq("layer_type", layer_type)
        .eq("topic", topic)
        .eq("is_deprecated", False)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def get_active_belief(client, *, layer_type: str, topic: str) -> dict[str, Any] | None:
    response = (
        client.table("core_identity_beliefs")
        .select("*")
        .eq("layer_type", layer_type)
        .eq("topic", topic)
        .eq("is_deprecated", False)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def belief_text_differs(existing: str, new: str) -> bool:
    old_norm = existing.strip().lower()
    new_norm = new.strip().lower()
    if not new_norm:
        return False
    if old_norm == new_norm:
        return False
    return new_norm not in old_norm and old_norm not in new_norm


def decision_exists(client, *, event_title: str) -> bool:
    response = (
        client.table("life_experience_decisions")
        .select("id")
        .eq("event_title", event_title)
        .limit(1)
        .execute()
    )
    return bool(response.data)


def insert_identity_item(
    client,
    item: dict[str, Any],
    *,
    allow_update: bool = False,
) -> str:
    topic = item["topic"]
    rules = item["rules_and_values"]
    existing = get_active_belief(client, layer_type="identity", topic=topic)
    if existing:
        if allow_update and belief_text_differs(
            existing.get("rules_and_values", ""), rules
        ):
            client.table("core_identity_beliefs").insert(
                {
                    "layer_type": "identity",
                    "topic": topic,
                    "rules_and_values": rules,
                }
            ).execute()
            return "updated"
        return "skipped"
    client.table("core_identity_beliefs").insert(
        {
            "layer_type": "identity",
            "topic": topic,
            "rules_and_values": rules,
        }
    ).execute()
    return "inserted"


def insert_belief_item(
    client,
    item: dict[str, Any],
    *,
    allow_update: bool = False,
) -> str:
    topic = item["topic"]
    rules = item["rules_and_values"]
    existing = get_active_belief(client, layer_type="beliefs", topic=topic)
    if existing:
        if allow_update and belief_text_differs(
            existing.get("rules_and_values", ""), rules
        ):
            client.table("core_identity_beliefs").insert(
                {
                    "layer_type": "beliefs",
                    "topic": topic,
                    "rules_and_values": rules,
                }
            ).execute()
            return "updated"
        return "skipped"
    client.table("core_identity_beliefs").insert(
        {
            "layer_type": "beliefs",
            "topic": topic,
            "rules_and_values": rules,
        }
    ).execute()
    return "inserted"


def insert_decision_or_regret(
    client,
    item: dict[str, Any],
    *,
    is_regret: bool,
    embed_fn=embed_text,
) -> str:
    title = item["event_title"]
    if decision_exists(client, event_title=title):
        return "skipped"
    text_for_embed = "\n".join(
        [
            item.get("event_title", ""),
            item.get("context", ""),
            item.get("choice_made", ""),
            item.get("consequences_lessons", ""),
        ]
    )
    client.table("life_experience_decisions").insert(
        {
            "event_title": title,
            "context": item["context"],
            "choice_made": item["choice_made"],
            "consequences_lessons": item["consequences_lessons"],
            "is_regret": item.get("is_regret", is_regret),
            "event_timestamp": parse_timestamp(item.get("event_timestamp")),
            "embedding": embed_fn(text_for_embed),
        }
    ).execute()
    return "inserted"


def insert_style_patterns(
    client,
    style: dict[str, Any],
    *,
    pattern_types: set[str] | None = None,
    confidence: float = 0.6,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pattern_type, values in style.items():
        if pattern_types is not None and pattern_type not in pattern_types:
            continue
        if not values:
            continue
        payload_values = values if isinstance(values, list) else [values]
        client.table("style_patterns").insert(
            {
                "pattern_type": pattern_type,
                "data_payload": {"values": payload_values},
                "confidence_score": confidence,
            }
        ).execute()
        counts[pattern_type] = counts.get(pattern_type, 0) + 1
    return counts


def seed_profile_data(
    profile: dict[str, Any],
    *,
    allow_belief_updates: bool = False,
    embed_fn=embed_text,
) -> dict[str, Any]:
    """Load a full personality profile dict into Supabase (batch seed)."""
    client = get_supabase()
    counts = {"identity": 0, "beliefs": 0, "decisions": 0, "regrets": 0, "style": 0}
    skipped = {"identity": 0, "beliefs": 0, "decisions": 0, "regrets": 0, "style": 0}

    for item in profile.get("identity", []):
        result = insert_identity_item(client, item, allow_update=allow_belief_updates)
        if result == "inserted":
            counts["identity"] += 1
        elif result == "updated":
            counts["identity"] += 1
        else:
            skipped["identity"] += 1

    for item in profile.get("beliefs", []):
        result = insert_belief_item(client, item, allow_update=allow_belief_updates)
        if result == "inserted":
            counts["beliefs"] += 1
        elif result == "updated":
            counts["beliefs"] += 1
        else:
            skipped["beliefs"] += 1

    for bucket, is_regret in (("decisions", False), ("regrets", True)):
        for item in profile.get(bucket, []):
            result = insert_decision_or_regret(
                client, item, is_regret=is_regret, embed_fn=embed_fn
            )
            if result == "inserted":
                counts[bucket] += 1
            else:
                skipped[bucket] += 1

    style_counts = insert_style_patterns(client, profile.get("style", {}))
    counts["style"] = sum(style_counts.values())

    return {"inserted": counts, "skipped_duplicates": skipped}
