"""Vector retrieval with recency reranking."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from src.config import get_settings
from src.db.supabase_client import get_supabase
from src.embeddings.cohere import (
    CohereEmbedRateLimitError,
    embed_text as embed_query_text,
)
from src.embeddings.gemini import embed_text as embed_gemini_text

logger = logging.getLogger(__name__)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def recency_score(
    similarity: float, timestamp: datetime | None, *, lambda_coef: float
) -> float:
    if timestamp is None:
        return similarity
    now = datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86_400, 0.0)
    return similarity * math.exp(-lambda_coef * age_days)


def retrieve_knowledge(
    query: str, *, limit: int = 5, category: str | None = None
) -> list[dict[str, Any]]:
    settings = get_settings()
    try:
        embedding = embed_query_text(query, input_type="search_query", max_retries=1)
    except CohereEmbedRateLimitError:
        logger.warning("Cohere rate limit — knowledge RAG skipped for this request")
        return []
    client = get_supabase()

    response = client.rpc(
        "match_knowledge_chunks",
        {
            "query_embedding": embedding,
            "match_count": limit,
            "filter_category": category,
        },
    ).execute()

    rows = response.data or []
    for row in rows:
        row["rerank_score"] = recency_score(
            float(row.get("similarity", 0)),
            _parse_timestamp(row.get("created_at")),
            lambda_coef=settings.recency_lambda,
        )
    return sorted(rows, key=lambda item: item["rerank_score"], reverse=True)


def retrieve_life_experience(
    query: str,
    *,
    limit: int = 5,
    include_regrets: bool = True,
) -> list[dict[str, Any]]:
    settings = get_settings()
    embedding = embed_gemini_text(query)
    client = get_supabase()

    response = client.rpc(
        "match_life_experience",
        {
            "query_embedding": embedding,
            "match_count": limit,
            "include_regrets": include_regrets,
        },
    ).execute()

    rows = response.data or []
    for row in rows:
        row["rerank_score"] = recency_score(
            float(row.get("similarity", 0)),
            _parse_timestamp(row.get("event_timestamp")),
            lambda_coef=settings.recency_lambda,
        )
    return sorted(rows, key=lambda item: item["rerank_score"], reverse=True)


def fetch_identity_beliefs() -> list[dict[str, Any]]:
    client = get_supabase()
    response = (
        client.table("core_identity_beliefs")
        .select("*")
        .eq("is_deprecated", False)
        .order("updated_at", desc=True)
        .execute()
    )
    return response.data or []


def fetch_style_patterns(
    *, query: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    settings = get_settings()
    client = get_supabase()
    response = (
        client.table("style_patterns")
        .select("*")
        .order("confidence_score", desc=True)
        .limit(limit)
        .execute()
    )
    rows = response.data or []
    if not query:
        return rows

    # Optional recency rerank when updated_at is present (style feedback loop)
    for row in rows:
        similarity = float(row.get("confidence_score", 0.5))
        row["rerank_score"] = recency_score(
            similarity,
            _parse_timestamp(row.get("updated_at")),
            lambda_coef=settings.recency_lambda,
        )
    return sorted(rows, key=lambda item: item.get("rerank_score", 0), reverse=True)


def fetch_active_persona_rules(
    *,
    query: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch confirmed author rules only; book profiles never enter this table."""
    client = get_supabase()
    global_response = (
        client.table("persona_rules")
        .select("*")
        .eq("status", "active")
        .in_(
            "rule_type",
            ["identity", "forbidden_phrase", "forbidden_topic", "content_preference"],
        )
        .order("updated_at", desc=True)
        .limit(min(limit, 10))
        .execute()
    )
    rows = list(global_response.data or [])
    if query and len(rows) < limit:
        try:
            embedding = embed_gemini_text(query)
            relevant = client.rpc(
                "match_persona_rules",
                {
                    "query_embedding": embedding,
                    "match_count": limit - len(rows),
                },
            ).execute()
            seen = {row["id"] for row in rows}
            rows.extend(
                row for row in (relevant.data or []) if row.get("id") not in seen
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Persona vector retrieval failed: %s", exc)
    if not rows:
        fallback = (
            client.table("persona_rules")
            .select("*")
            .eq("status", "active")
            .order("updated_at", desc=True)
            .limit(limit)
            .execute()
        )
        return fallback.data or []
    return rows[:limit]


def fetch_style_mode(slug: str) -> dict[str, Any] | None:
    response = (
        get_supabase()
        .table("style_modes")
        .select("*")
        .eq("slug", slug)
        .eq("enabled", True)
        .limit(1)
        .execute()
    )
    rows = response.data or []
    return rows[0] if rows else None


def retrieve_voice_examples(query: str, *, limit: int = 6) -> list[dict[str, Any]]:
    client = get_supabase()
    try:
        embedding = embed_gemini_text(query)
        response = client.rpc(
            "match_voice_examples",
            {"query_embedding": embedding, "match_count": limit},
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Voice vector retrieval failed, using weighted fallback: %s", exc
        )
        fallback = (
            client.table("voice_examples")
            .select("id,text,source_type,source_chat,occurred_at,quality_weight")
            .eq("status", "active")
            .order("quality_weight", desc=True)
            .order("occurred_at", desc=True)
            .limit(limit)
            .execute()
        )
        return fallback.data or []
    rows = response.data or []
    for row in rows:
        similarity = float(row.get("similarity", 0))
        row["rerank_score"] = recency_score(
            similarity * float(row.get("quality_weight", 0.5)),
            _parse_timestamp(row.get("occurred_at")),
            lambda_coef=get_settings().recency_lambda,
        )
    return sorted(rows, key=lambda item: item["rerank_score"], reverse=True)


def retrieve_confirmed_experiences(
    query: str,
    *,
    limit: int = 4,
    include_regrets: bool = True,
) -> list[dict[str, Any]]:
    client = get_supabase()
    try:
        embedding = embed_gemini_text(query)
        response = client.rpc(
            "match_life_experiences",
            {
                "query_embedding": embedding,
                "match_count": limit,
                "include_regrets": include_regrets,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Experience vector retrieval failed, using recent fallback: %s", exc
        )
        query_builder = (
            client.table("life_experiences")
            .select(
                "id,event_title,context,choice_made,consequences_lessons,"
                "is_regret,event_timestamp"
            )
            .eq("status", "active")
        )
        if not include_regrets:
            query_builder = query_builder.eq("is_regret", False)
        fallback = (
            query_builder.order("event_timestamp", desc=True).limit(limit).execute()
        )
        return fallback.data or []
    rows = response.data or []
    for row in rows:
        row["rerank_score"] = recency_score(
            float(row.get("similarity", 0)),
            _parse_timestamp(row.get("event_timestamp")),
            lambda_coef=get_settings().recency_lambda,
        )
    return sorted(rows, key=lambda item: item["rerank_score"], reverse=True)


def retrieve_knowledge_v21(
    query: str,
    *,
    limit: int = 3,
    verified_only: bool = False,
) -> list[dict[str, Any]]:
    try:
        embedding = embed_query_text(query, input_type="search_query", max_retries=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Knowledge embedding unavailable — RAG skipped: %s", exc)
        return []
    response = (
        get_supabase()
        .rpc(
            "match_knowledge_chunks_v21",
            {
                "query_embedding": embedding,
                "match_count": limit,
                "verified_only": verified_only,
            },
        )
        .execute()
    )
    return response.data or []
