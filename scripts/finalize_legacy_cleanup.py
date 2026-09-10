#!/usr/bin/env python3
"""Physically remove archived legacy persona rows after Telegram review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

from src.db.supabase_client import get_supabase  # noqa: E402

CONFIRMATION = "DELETE REVIEWED LEGACY PERSONA"


def _pending_count(table: str) -> int:
    response = (
        get_supabase()
        .table(table)
        .select("id", count="exact")
        .eq("status", "pending")
        .limit(1)
        .execute()
    )
    return int(response.count or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize legacy persona cleanup.")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise SystemExit(f"Refusing cleanup. Pass --confirm '{CONFIRMATION}'")

    pending = _pending_count("persona_rules") + _pending_count("life_experiences")
    if pending:
        raise SystemExit(f"Refusing cleanup: {pending} review items are still pending")

    client = get_supabase()
    client.table("core_identity_beliefs").delete().filter(
        "source_book", "not.is", "null"
    ).execute()
    client.table("core_identity_beliefs").delete().eq("is_deprecated", True).execute()
    client.table("style_patterns").delete().filter(
        "source_book", "not.is", "null"
    ).execute()
    print("Legacy book persona rows and deprecated duplicates were deleted.")


if __name__ == "__main__":
    main()
