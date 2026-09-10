#!/usr/bin/env python3
"""List or verify islam knowledge chunks for V2.1 religious guardrail."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

from src.db.memory_repository import update_knowledge_verification  # noqa: E402
from src.db.supabase_client import get_supabase  # noqa: E402


def fetch_islam_chunks(*, status: str, limit: int) -> list[dict]:
    response = (
        get_supabase()
        .table("knowledge_chunks")
        .select("id, content, source_book, chapter_title, verification_status")
        .eq("category", "islam")
        .eq("verification_status", status)
        .order("id")
        .limit(limit)
        .execute()
    )
    return response.data or []


def main() -> int:
    parser = argparse.ArgumentParser(description="Islam knowledge verification helper")
    parser.add_argument(
        "--status",
        default="unverified",
        choices=("unverified", "verified", "rejected"),
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mark listed chunks as verified (owner-reviewed)",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help='Required with --apply: pass "VERIFY ISLAM CHUNKS"',
    )
    args = parser.parse_args()

    rows = fetch_islam_chunks(status=args.status, limit=args.limit)
    if not rows:
        print(f"No islam chunks with status={args.status}.")
        return 0

    print(f"Found {len(rows)} islam chunk(s) [{args.status}]:")
    for row in rows:
        preview = (row.get("content") or "")[:120].replace("\n", " ")
        print(
            f"  id={row['id']} book={row.get('source_book') or '—'} "
            f"preview={preview!r}..."
        )

    if not args.apply:
        print("\nDry-run only. To verify: --apply --confirm \"VERIFY ISLAM CHUNKS\"")
        return 0

    if args.confirm != "VERIFY ISLAM CHUNKS":
        print('Refusing --apply without --confirm "VERIFY ISLAM CHUNKS"', file=sys.stderr)
        return 1

    ids = [int(row["id"]) for row in rows]
    updated = update_knowledge_verification(ids=ids, status="verified")
    print(f"Verified {updated} chunk(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
