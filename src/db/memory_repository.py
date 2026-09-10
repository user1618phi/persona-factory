"""Persistence primitives for Persona Factory V2.1."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from src.db.supabase_client import get_supabase

RULE_SEMANTIC_DUPLICATE_THRESHOLD = 0.85
EXPERIENCE_SEMANTIC_DUPLICATE_THRESHOLD = 0.86


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def fingerprint(*parts: Any) -> str:
    normalized = ":".join(
        normalize_text(json.dumps(part, ensure_ascii=False, sort_keys=True))
        if isinstance(part, (dict, list))
        else normalize_text(str(part or ""))
        for part in parts
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_draft(
    *,
    owner_user_id: int,
    raw_input: str,
    intent: str = "develop",
    length_mode: str = "short",
    style_mode: str = "my_voice",
    journal_id: int | None = None,
) -> dict[str, Any]:
    client = get_supabase()
    response = client.table("drafts").insert(
        {
            "owner_user_id": owner_user_id,
            "raw_input": raw_input,
            "intent": intent,
            "length_mode": length_mode,
            "style_mode": style_mode,
            "state": "configuring",
            "journal_id": journal_id,
        }
    ).execute()
    return (response.data or [])[0]


def get_draft(draft_id: str | UUID) -> dict[str, Any] | None:
    response = (
        get_supabase()
        .table("drafts")
        .select("*")
        .eq("id", str(draft_id))
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def update_draft(draft_id: str | UUID, **values: Any) -> dict[str, Any] | None:
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    response = (
        get_supabase()
        .table("drafts")
        .update(values)
        .eq("id", str(draft_id))
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else get_draft(draft_id)


def latest_draft_for_owner(
    owner_user_id: int,
    *,
    states: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    query = (
        get_supabase()
        .table("drafts")
        .select("*")
        .eq("owner_user_id", owner_user_id)
    )
    if states:
        query = query.in_("state", list(states))
    response = query.order("updated_at", desc=True).limit(1).execute()
    rows = response.data or []
    return rows[0] if rows else None


def add_draft_version(
    draft_id: str | UUID,
    *,
    text: str,
    change_type: str,
    instruction: str | None = None,
) -> dict[str, Any]:
    client = get_supabase()
    existing = (
        client.table("draft_versions")
        .select("version_number")
        .eq("draft_id", str(draft_id))
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    version_number = ((existing.data or [{}])[0].get("version_number") or 0) + 1
    response = client.table("draft_versions").insert(
        {
            "draft_id": str(draft_id),
            "version_number": version_number,
            "text": text,
            "change_type": change_type,
            "instruction": instruction,
        }
    ).execute()
    update_draft(draft_id, final_text=text, state="drafted", pending_instruction=None)
    return (response.data or [])[0]


def latest_draft_version(draft_id: str | UUID) -> dict[str, Any] | None:
    response = (
        get_supabase()
        .table("draft_versions")
        .select("*")
        .eq("draft_id", str(draft_id))
        .order("version_number", desc=True)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def record_generation_run(payload: dict[str, Any]) -> None:
    get_supabase().table("generation_runs").insert(payload).execute()


def _rule_content_from_candidate(content: dict[str, Any]) -> str:
    return str(
        content.get("content")
        or content.get("rules_and_values")
        or content.get("value")
        or json.dumps(content, ensure_ascii=False)
    )


def _merge_metadata(row: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    metadata.update(patch)
    return metadata


def _find_active_rule_duplicate(
    client: Any,
    *,
    rule_type: str,
    content: str,
    embedding: list[float] | None,
    exclude_id: int | None = None,
) -> dict[str, Any] | None:
    normalized = normalize_text(content)
    response = (
        client.table("persona_rules")
        .select("id,rule_type,topic,content")
        .eq("status", "active")
        .eq("rule_type", rule_type)
        .execute()
    )
    for row in response.data or []:
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        if normalize_text(str(row.get("content") or "")) == normalized:
            return {
                "match_type": "exact_content",
                "duplicate_of_rule_id": row.get("id"),
                "duplicate_topic": row.get("topic"),
                "similarity": 1.0,
            }

    if not embedding:
        return None
    try:
        matches = client.rpc(
            "match_persona_rules",
            {"query_embedding": embedding, "match_count": 8},
        ).execute()
    except Exception:  # noqa: BLE001
        return None
    for row in matches.data or []:
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        if row.get("rule_type") != rule_type:
            continue
        similarity = float(row.get("similarity") or 0)
        if similarity >= RULE_SEMANTIC_DUPLICATE_THRESHOLD:
            return {
                "match_type": "semantic_similarity",
                "duplicate_of_rule_id": row.get("id"),
                "duplicate_topic": row.get("topic"),
                "similarity": similarity,
            }
    return None


def _skip_duplicate_candidate(
    client: Any,
    *,
    item: dict[str, Any],
    user_id: int,
    duplicate: dict[str, Any],
    now: str,
) -> None:
    client.table("memory_candidates").update(
        {
            "status": "skipped",
            "reviewed_at": now,
            "reviewed_by": user_id,
            "metadata": _merge_metadata(
                item,
                {
                    "skip_reason": "duplicate_active_persona_rule",
                    "duplicate_rule": duplicate,
                },
            ),
        }
    ).eq("id", item["id"]).execute()


def _experience_text(row: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(row.get("event_title") or row.get("topic") or "Опыт"),
            str(row.get("context") or ""),
            str(row.get("choice_made") or ""),
            str(row.get("consequences_lessons") or ""),
        ]
    )


def _find_active_experience_duplicate(
    client: Any,
    *,
    row: dict[str, Any],
    embedding: list[float] | None,
    is_regret: bool,
    exclude_id: int | None = None,
) -> dict[str, Any] | None:
    normalized = normalize_text(_experience_text(row))
    response = (
        client.table("life_experiences")
        .select("id,event_title,context,choice_made,consequences_lessons,is_regret")
        .eq("status", "active")
        .eq("is_regret", is_regret)
        .execute()
    )
    for existing in response.data or []:
        if exclude_id is not None and existing.get("id") == exclude_id:
            continue
        if normalize_text(_experience_text(existing)) == normalized:
            return {
                "match_type": "exact_experience",
                "duplicate_of_experience_id": existing.get("id"),
                "duplicate_title": existing.get("event_title"),
                "similarity": 1.0,
            }

    if not embedding:
        return None
    try:
        matches = client.rpc(
            "match_life_experiences",
            {
                "query_embedding": embedding,
                "match_count": 8,
                "include_regrets": True,
            },
        ).execute()
    except Exception:  # noqa: BLE001
        return None
    for match in matches.data or []:
        if exclude_id is not None and match.get("id") == exclude_id:
            continue
        if bool(match.get("is_regret")) != is_regret:
            continue
        similarity = float(match.get("similarity") or 0)
        if similarity >= EXPERIENCE_SEMANTIC_DUPLICATE_THRESHOLD:
            return {
                "match_type": "semantic_experience_similarity",
                "duplicate_of_experience_id": match.get("id"),
                "duplicate_title": match.get("event_title"),
                "similarity": similarity,
            }
    return None


def _skip_duplicate_experience_candidate(
    client: Any,
    *,
    item: dict[str, Any],
    user_id: int,
    duplicate: dict[str, Any],
    now: str,
) -> None:
    client.table("memory_candidates").update(
        {
            "status": "skipped",
            "reviewed_at": now,
            "reviewed_by": user_id,
            "metadata": _merge_metadata(
                item,
                {
                    "skip_reason": "duplicate_active_life_experience",
                    "duplicate_experience": duplicate,
                },
            ),
        }
    ).eq("id", item["id"]).execute()


def fetch_knowledge_review_queue(
    *,
    category: str | None = "islam",
    status: str = "unverified",
    limit: int = 50,
) -> list[dict[str, Any]]:
    query = (
        get_supabase()
        .table("knowledge_chunks")
        .select(
            "id,content,category,source_book,canonical_source,chapter_title,"
            "verification_status,created_at"
        )
        .eq("verification_status", status)
        .order("created_at", desc=True)
        .limit(max(1, min(limit, 200)))
    )
    if category and category != "all":
        query = query.eq("category", category)
    return query.execute().data or []


def update_knowledge_verification(
    *,
    ids: list[int],
    status: str,
) -> int:
    if status not in {"verified", "rejected", "unverified"}:
        raise ValueError("Invalid verification status")
    clean_ids = sorted({int(row_id) for row_id in ids if int(row_id) > 0})
    if not clean_ids:
        return 0
    response = (
        get_supabase()
        .table("knowledge_chunks")
        .update({"verification_status": status})
        .in_("id", clean_ids)
        .execute()
    )
    return len(response.data or clean_ids)


def create_memory_candidate(
    *,
    candidate_type: str,
    topic: str | None,
    content: dict[str, Any],
    source_type: str,
    source_id: str | None,
    source_quote: str | None,
    confidence: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    payload = {
        "candidate_type": candidate_type,
        "topic": topic,
        "content": content,
        "source_type": source_type,
        "source_id": source_id,
        "source_quote": source_quote,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "status": "pending",
        "fingerprint": fingerprint(candidate_type, topic, content),
        "metadata": metadata or {},
    }
    response = (
        get_supabase()
        .table("memory_candidates")
        .upsert(payload, on_conflict="fingerprint", ignore_duplicates=True)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def next_review_item() -> dict[str, Any] | None:
    client = get_supabase()
    for kind, table in (
        ("candidate", "memory_candidates"),
        ("rule", "persona_rules"),
        ("experience", "life_experiences"),
    ):
        response = (
            client.table(table)
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if rows:
            return {"kind": kind, "record": rows[0]}
    return None


def reject_review_item(kind: str, record_id: int, user_id: int) -> None:
    table = {
        "candidate": "memory_candidates",
        "rule": "persona_rules",
        "experience": "life_experiences",
    }[kind]
    values: dict[str, Any] = {"status": "rejected"}
    if kind == "candidate":
        values.update(
            {
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "reviewed_by": user_id,
            }
        )
    else:
        values.update(
            {
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                "confirmed_by": user_id,
            }
        )
    get_supabase().table(table).update(values).eq("id", record_id).execute()


def skip_review_item(kind: str, record_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    table = {
        "candidate": "memory_candidates",
        "rule": "persona_rules",
        "experience": "life_experiences",
    }[kind]
    timestamp_field = "created_at" if kind == "candidate" else "updated_at"
    get_supabase().table(table).update({timestamp_field: now}).eq(
        "id", record_id
    ).execute()


def edit_review_item(kind: str, record_id: int, text: str) -> None:
    client = get_supabase()
    if kind == "rule":
        client.table("persona_rules").update({"content": text}).eq("id", record_id).execute()
        return
    if kind == "experience":
        client.table("life_experiences").update(
            {"consequences_lessons": text}
        ).eq("id", record_id).execute()
        return
    response = (
        client.table("memory_candidates")
        .select("candidate_type,content")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return
    row = rows[0]
    content = dict(row.get("content") or {})
    if row.get("candidate_type") in {"experience", "regret"}:
        content["consequences_lessons"] = text
    else:
        content["content"] = text
        content["value"] = text
    client.table("memory_candidates").update({"content": content}).eq(
        "id", record_id
    ).execute()


def accept_review_item(kind: str, record_id: int, user_id: int) -> str | None:
    client = get_supabase()
    now = datetime.now(timezone.utc).isoformat()
    if kind == "rule":
        response = (
            client.table("persona_rules")
            .select("id,rule_type,topic,content,embedding,metadata")
            .eq("id", record_id)
            .limit(1)
            .execute()
        )
        row = (response.data or [{}])[0]
        values: dict[str, Any] = {
            "status": "active",
            "confirmed_at": now,
            "confirmed_by": user_id,
        }
        if not row.get("embedding"):
            from src.embeddings.gemini import embed_text

            values["embedding"] = embed_text(
                f"{row.get('topic') or ''}\n{row.get('content') or ''}"
            )
        duplicate = _find_active_rule_duplicate(
            client,
            rule_type=row.get("rule_type") or "belief",
            content=str(row.get("content") or ""),
            embedding=values.get("embedding") or row.get("embedding"),
            exclude_id=record_id,
        )
        if duplicate:
            client.table("persona_rules").update(
                {
                    "status": "rejected",
                    "confirmed_at": now,
                    "confirmed_by": user_id,
                    "metadata": _merge_metadata(
                        row,
                        {
                            "reject_reason": "duplicate_active_persona_rule",
                            "duplicate_rule": duplicate,
                        },
                    ),
                }
            ).eq("id", record_id).execute()
            return "skipped_duplicate"
        client.table("persona_rules").update(
            values
        ).eq("id", record_id).execute()
        return "accepted"
    if kind == "experience":
        response = (
            client.table("life_experiences")
            .select("*,metadata")
            .eq("id", record_id)
            .limit(1)
            .execute()
        )
        row = (response.data or [{}])[0]
        values = {"status": "active", "confirmed_at": now, "confirmed_by": user_id}
        if not row.get("embedding"):
            from src.embeddings.gemini import embed_text

            values["embedding"] = embed_text(_experience_text(row))
        duplicate = _find_active_experience_duplicate(
            client,
            row=row,
            embedding=values.get("embedding") or row.get("embedding"),
            is_regret=bool(row.get("is_regret")),
            exclude_id=record_id,
        )
        if duplicate:
            client.table("life_experiences").update(
                {
                    "status": "rejected",
                    "confirmed_at": now,
                    "confirmed_by": user_id,
                    "metadata": _merge_metadata(
                        row,
                        {
                            "reject_reason": "duplicate_active_life_experience",
                            "duplicate_experience": duplicate,
                        },
                    ),
                }
            ).eq("id", record_id).execute()
            return "skipped_duplicate"
        client.table("life_experiences").update(values).eq("id", record_id).execute()
        return "accepted"

    response = (
        client.table("memory_candidates")
        .select("*")
        .eq("id", record_id)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    if not rows:
        return
    item = rows[0]
    candidate_type = item["candidate_type"]
    content = item.get("content") or {}

    if candidate_type in {"experience", "regret"}:
        from src.embeddings.gemini import embed_text

        experience_text = _experience_text(
            {
                **content,
                "topic": item.get("topic"),
            }
        )
        experience_embedding = embed_text(experience_text)
        duplicate = _find_active_experience_duplicate(
            client,
            row={**content, "topic": item.get("topic")},
            embedding=experience_embedding,
            is_regret=candidate_type == "regret",
        )
        if duplicate:
            _skip_duplicate_experience_candidate(
                client,
                item=item,
                user_id=user_id,
                duplicate=duplicate,
                now=now,
            )
            return "skipped_duplicate"

        client.table("life_experiences").upsert(
            {
                "event_title": content.get("event_title") or item.get("topic") or "Опыт",
                "context": content.get("context") or "",
                "choice_made": content.get("choice_made") or "",
                "consequences_lessons": content.get("consequences_lessons") or "",
                "is_regret": candidate_type == "regret",
                "event_timestamp": content.get("event_timestamp"),
                "embedding": experience_embedding,
                "source_type": (
                    "channel_post" if item["source_type"] == "channel_post" else "note"
                ),
                "source_id": item.get("source_id"),
                "source_quote": item.get("source_quote"),
                "confidence": item.get("confidence", 0.5),
                "status": "active",
                "fingerprint": fingerprint(candidate_type, content),
                "confirmed_at": now,
                "confirmed_by": user_id,
                "metadata": {"candidate_id": record_id},
            },
            on_conflict="fingerprint",
        ).execute()
    else:
        rule_type = candidate_type
        rule_content = _rule_content_from_candidate(content)
        from src.embeddings.gemini import embed_text

        rule_embedding = embed_text(
            f"{item.get('topic') or rule_type}\n{rule_content}"
        )
        duplicate = _find_active_rule_duplicate(
            client,
            rule_type=rule_type,
            content=rule_content,
            embedding=rule_embedding,
        )
        if duplicate:
            _skip_duplicate_candidate(
                client,
                item=item,
                user_id=user_id,
                duplicate=duplicate,
                now=now,
            )
            return "skipped_duplicate"

        client.table("persona_rules").upsert(
            {
                "rule_type": rule_type,
                "topic": item.get("topic") or content.get("topic") or rule_type,
                "content": rule_content,
                "embedding": rule_embedding,
                "source_type": (
                    "correction"
                    if item["source_type"] == "correction"
                    else "channel_post"
                    if item["source_type"] == "channel_post"
                    else "note"
                ),
                "source_id": item.get("source_id"),
                "source_quote": item.get("source_quote"),
                "confidence": item.get("confidence", 0.5),
                "status": "active",
                "fingerprint": fingerprint(rule_type, item.get("topic"), content),
                "confirmed_at": now,
                "confirmed_by": user_id,
                "metadata": {"candidate_id": record_id},
            },
            on_conflict="fingerprint",
        ).execute()

    client.table("memory_candidates").update(
        {"status": "accepted", "reviewed_at": now, "reviewed_by": user_id}
    ).eq("id", record_id).execute()
    return "accepted"


def insert_voice_example(
    *,
    text: str,
    source_type: str,
    source_external_id: str | None,
    source_chat: str | None,
    occurred_at: str | None,
    quality_weight: float,
    embedding: list[float] | None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    payload = {
        "text": text,
        "embedding": embedding,
        "source_type": source_type,
        "source_external_id": source_external_id,
        "source_chat": source_chat,
        "occurred_at": occurred_at,
        "quality_weight": quality_weight,
        "status": "active",
        "fingerprint": fingerprint(source_type, text),
        "metadata": metadata or {},
    }
    response = (
        get_supabase()
        .table("voice_examples")
        .upsert(payload, on_conflict="fingerprint", ignore_duplicates=True)
        .execute()
    )
    return bool(response.data)
