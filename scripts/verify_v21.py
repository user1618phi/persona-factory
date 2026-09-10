#!/usr/bin/env python3
"""Read-only post-migration verification for Persona Factory V2.1."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

from src.db.supabase_client import get_supabase  # noqa: E402


def count(table: str, **filters) -> int:
    query = get_supabase().table(table).select("id", count="exact")
    for key, value in filters.items():
        query = query.eq(key, value)
    return int(query.limit(1).execute().count or 0)


def _duplicate_count(rows: list[dict], key: str) -> int:
    values = [row.get(key) for row in rows if row.get(key)]
    return len(values) - len(set(values))


def _published_draft_inconsistencies() -> int:
    client = get_supabase()
    posts = (
        client.table("bot_published_posts")
        .select("draft_id")
        .filter("draft_id", "not.is", "null")
        .execute()
        .data
        or []
    )
    draft_ids = sorted({str(row["draft_id"]) for row in posts if row.get("draft_id")})
    if not draft_ids:
        return 0
    drafts = (
        client.table("drafts").select("id,state").in_("id", draft_ids).execute().data
        or []
    )
    states = {str(row["id"]): row.get("state") for row in drafts}
    return sum(states.get(draft_id) != "published" for draft_id in draft_ids)


def build_report() -> dict:
    client = get_supabase()
    knowledge_rows = (
        client.table("knowledge_chunks")
        .select("id,fingerprint,embedding")
        .execute()
        .data
        or []
    )
    voice_rows = (
        client.table("voice_examples").select("id,fingerprint,embedding").execute().data
        or []
    )
    result = {
        "persona_rules": {
            "total": count("persona_rules"),
            "active": count("persona_rules", status="active"),
            "pending": count("persona_rules", status="pending"),
        },
        "voice_examples": {
            "total": count("voice_examples"),
            "channel_posts": count("voice_examples", source_type="channel_post"),
            "telegram_chats": count("voice_examples", source_type="telegram_chat"),
        },
        "life_experiences": {
            "total": count("life_experiences"),
            "active": count("life_experiences", status="active"),
            "pending": count("life_experiences", status="pending"),
        },
        "memory_candidates": {
            "pending": count("memory_candidates", status="pending"),
            "accepted": count("memory_candidates", status="accepted"),
            "rejected": count("memory_candidates", status="rejected"),
        },
        "drafts": {
            "total": count("drafts"),
            "published_state_inconsistencies": _published_draft_inconsistencies(),
        },
        "legacy": {
            "active_book_beliefs": int(
                client.table("core_identity_beliefs")
                .select("id", count="exact")
                .eq("is_deprecated", False)
                .filter("source_book", "not.is", "null")
                .limit(1)
                .execute()
                .count
                or 0
            ),
            "book_style_rows": int(
                client.table("style_patterns")
                .select("id", count="exact")
                .filter("source_book", "not.is", "null")
                .limit(1)
                .execute()
                .count
                or 0
            ),
        },
        "knowledge": {
            "total": count("knowledge_chunks"),
            "verified": count("knowledge_chunks", verification_status="verified"),
            "duplicate_fingerprints": _duplicate_count(knowledge_rows, "fingerprint"),
            "null_embeddings": sum(
                row.get("embedding") is None for row in knowledge_rows
            ),
        },
        "voice": {
            "duplicate_fingerprints": _duplicate_count(voice_rows, "fingerprint"),
            "null_embeddings": sum(row.get("embedding") is None for row in voice_rows),
        },
        "style_modes": count("style_modes", enabled=True),
    }
    return result


def evaluate_checks(result: dict, *, mode: str) -> dict[str, bool]:
    common = {
        "no_active_book_beliefs": result["legacy"]["active_book_beliefs"] == 0,
        "voice_corpus_loaded": (
            result["voice_examples"]["channel_posts"] >= 62
            and result["voice_examples"]["telegram_chats"] >= 1800
        ),
        "four_style_modes": result["style_modes"] == 4,
        "knowledge_has_no_duplicates": result["knowledge"]["duplicate_fingerprints"]
        == 0,
        "voice_has_no_duplicates": result["voice"]["duplicate_fingerprints"] == 0,
        "knowledge_embeddings_present": result["knowledge"]["null_embeddings"] == 0,
        "voice_embeddings_present": result["voice"]["null_embeddings"] == 0,
        "published_drafts_are_consistent": result["drafts"][
            "published_state_inconsistencies"
        ]
        == 0,
    }
    if mode == "pre-review":
        common["legacy_persona_waits_for_review"] = (
            result["persona_rules"]["pending"] >= 10
            and result["life_experiences"]["pending"] >= 6
        )
    else:
        common["pending_all_review_is_empty"] = (
            result["persona_rules"]["pending"]
            + result["life_experiences"]["pending"]
            + result["memory_candidates"]["pending"]
            == 0
        )
    return common


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "--pre-review",
        action="store_true",
        help="Use the old rollout gate where legacy persona records are expected to wait for Telegram review.",
    )
    parser.add_argument(
        "--post-review",
        action="store_true",
        help="Use the production gate after Telegram review and cleanup. This is the default.",
    )
    args = parser.parse_args()
    mode = "pre-review" if args.pre_review else "post-review"
    result = build_report()
    result["mode"] = mode
    result["checks"] = evaluate_checks(result, mode=mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(result["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
