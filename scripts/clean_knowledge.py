#!/usr/bin/env python3
"""Plan or apply conservative cleanup of problematic knowledge chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

from src.db.supabase_client import get_supabase  # noqa: E402
from src.embeddings.cohere import embed_texts  # noqa: E402

MIN_CHARS = 100
MAX_CHARS = 2500
LONG_THRESHOLD = 5000
CANONICAL_SOURCES = {
    "627d32b823571642642689": "Пиши, сокращай",
    "launch read stamped": "Launch by Michael Stelzner",
    "Launch by Michael Stelzner": "Launch by Michael Stelzner",
    "the hero and the outlaw": "The Hero and the Outlaw",
    "the story factor": "The Story Factor",
    "покажи свою работу остин клеон": "Покажи свою работу",
    "islam discipline excerpt": "Islam Discipline — curated",
}
KNOWLEDGE_SELECT_COLUMNS = (
    "id,content,category,source_book,canonical_source,chapter_title,"
    "chunk_index,verification_status,metadata,fingerprint"
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def digest(text: str) -> str:
    return hashlib.sha256(normalize(text).lower().encode("utf-8")).hexdigest()


def without_embedding(row: dict[str, Any], *, content: str) -> dict[str, Any]:
    return {
        **{key: value for key, value in row.items() if key != "embedding"},
        "content": content,
    }


def split_large(text: str, *, max_chars: int = MAX_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[start : start + max_chars].strip())
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def build_plan(rows: list[dict[str, Any]]) -> dict[str, Any]:
    delete_ids: set[int] = set()
    duplicate_groups = 0
    by_digest: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_digest[digest(row["content"])].append(row)
    for items in by_digest.values():
        if len(items) > 1:
            duplicate_groups += 1
            for duplicate in sorted(items, key=lambda item: item["id"])[1:]:
                delete_ids.add(int(duplicate["id"]))

    updates: dict[int, dict[str, Any]] = {}
    inserts: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if int(row["id"]) not in delete_ids:
            grouped[str(row.get("source_book") or "unknown")].append(row)

    merged_short = 0
    split_long = 0
    for source, items in grouped.items():
        items.sort(key=lambda item: (item.get("chunk_index") or 0, item["id"]))
        content_by_id = {int(item["id"]): str(item["content"]) for item in items}
        for index, row in enumerate(items):
            row_id = int(row["id"])
            content = content_by_id[row_id]
            if len(normalize(content)) >= MIN_CHARS or row_id in delete_ids:
                continue
            neighbors = items[index + 1 :] + list(reversed(items[:index]))
            target = next(
                (
                    item
                    for item in neighbors
                    if int(item["id"]) not in delete_ids
                    and item.get("chapter_title") == row.get("chapter_title")
                ),
                None,
            )
            if not target:
                continue
            target_id = int(target["id"])
            target_text = content_by_id[target_id]
            content_by_id[target_id] = (
                f"{content}\n\n{target_text}"
                if (target.get("chunk_index") or 0) >= (row.get("chunk_index") or 0)
                else f"{target_text}\n\n{content}"
            )
            updates[target_id] = without_embedding(
                target, content=content_by_id[target_id]
            )
            delete_ids.add(row_id)
            merged_short += 1

        for row in items:
            row_id = int(row["id"])
            if row_id in delete_ids:
                continue
            content = content_by_id[row_id]
            if len(content) <= LONG_THRESHOLD:
                if content != row["content"]:
                    updates[row_id] = without_embedding(row, content=content)
                continue
            parts = split_large(content)
            if len(parts) <= 1:
                continue
            split_long += 1
            updates[row_id] = without_embedding(row, content=parts[0])
            base_index = int(row.get("chunk_index") or 0)
            for offset, part in enumerate(parts[1:], start=1):
                inserts.append(
                    {
                        "content": part,
                        "category": row["category"],
                        "source_book": source,
                        "canonical_source": CANONICAL_SOURCES.get(source, source),
                        "chapter_title": row.get("chapter_title"),
                        "chunk_index": base_index * 1000 + offset,
                        "verification_status": row.get("verification_status")
                        or "unverified",
                        "metadata": {
                            **(row.get("metadata") or {}),
                            "split_from_id": row_id,
                            "cleanup_v21": True,
                        },
                    }
                )

    return {
        "delete_ids": sorted(delete_ids),
        "updates": list(updates.values()),
        "inserts": inserts,
        "stats": {
            "input_rows": len(rows),
            "duplicate_groups": duplicate_groups,
            "delete_rows": len(delete_ids),
            "merged_short": merged_short,
            "split_long": split_long,
            "updated_rows": len(updates),
            "inserted_rows": len(inserts),
        },
    }


def apply_plan(plan: dict[str, Any]) -> None:
    client = get_supabase()
    changed = plan["updates"] + plan["inserts"]
    vectors = embed_texts([row["content"] for row in changed])
    vector_index = 0

    for row in plan["updates"]:
        source = str(row.get("source_book") or "unknown")
        client.table("knowledge_chunks").update(
            {
                "content": row["content"],
                "embedding": vectors[vector_index],
                "canonical_source": CANONICAL_SOURCES.get(source, source),
                "fingerprint": digest(row["content"]),
                "metadata": {
                    **(row.get("metadata") or {}),
                    "cleanup_v21": True,
                },
            }
        ).eq("id", row["id"]).execute()
        vector_index += 1

    for row in plan["inserts"]:
        client.table("knowledge_chunks").insert(
            {
                **row,
                "embedding": vectors[vector_index],
                "fingerprint": digest(row["content"]),
            }
        ).execute()
        vector_index += 1

    for start in range(0, len(plan["delete_ids"]), 100):
        client.table("knowledge_chunks").delete().in_(
            "id", plan["delete_ids"][start : start + 100]
        ).execute()

    # Normalize untouched rows and fingerprints after destructive operations.
    response = (
        client.table("knowledge_chunks")
        .select("id,content,source_book,canonical_source,fingerprint")
        .execute()
    )
    for row in response.data or []:
        source = str(row.get("source_book") or "unknown")
        canonical = CANONICAL_SOURCES.get(source, source)
        value = digest(row["content"])
        if row.get("canonical_source") != canonical or row.get("fingerprint") != value:
            client.table("knowledge_chunks").update(
                {"canonical_source": canonical, "fingerprint": value}
            ).eq("id", row["id"]).execute()

    # Splitting can expose repeated boilerplate that was not an exact duplicate
    # before cleanup. Remove those duplicates before the unique index is created.
    final_rows = (
        client.table("knowledge_chunks")
        .select("id,fingerprint")
        .order("id")
        .execute()
        .data
        or []
    )
    seen: set[str] = set()
    final_duplicate_ids: list[int] = []
    for row in final_rows:
        value = row.get("fingerprint")
        if not value:
            continue
        if value in seen:
            final_duplicate_ids.append(int(row["id"]))
        else:
            seen.add(value)
    for start in range(0, len(final_duplicate_ids), 100):
        client.table("knowledge_chunks").delete().in_(
            "id", final_duplicate_ids[start : start + 100]
        ).execute()


def fetch_all_knowledge_chunks() -> list[dict[str, Any]]:
    client = get_supabase()
    rows: list[dict[str, Any]] = []
    page_size = 1000
    offset = 0
    while True:
        response = (
            client.table("knowledge_chunks")
            .select(KNOWLEDGE_SELECT_COLUMNS)
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean knowledge chunks safely.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "backups" / "knowledge_cleanup_plan.json",
    )
    args = parser.parse_args()
    rows = fetch_all_knowledge_chunks()
    plan = build_plan(rows)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(plan["stats"], ensure_ascii=False, indent=2))
    if args.apply:
        apply_plan(plan)
        print("Applied.")
    else:
        print(f"Dry run only. Plan: {args.report}")


if __name__ == "__main__":
    main()
