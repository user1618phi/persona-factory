#!/usr/bin/env python3
"""Import regret/reflection JSON as memory_candidates for /review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402

ensure_project_venv(__file__, ROOT)

from src.db.memory_repository import create_memory_candidate  # noqa: E402


def load_regrets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        regrets = payload.get("regrets") or []
    elif isinstance(payload, list):
        regrets = payload
    else:
        raise ValueError("JSON must be object with 'regrets' array or a list")
    if not isinstance(regrets, list):
        raise ValueError("'regrets' must be a list")
    return regrets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import regrets JSON (see docs/REGRET_CHATGPT_PROMPT.md)"
    )
    parser.add_argument("json_path", type=Path, help="Path to regrets JSON file")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print entries without creating candidates",
    )
    args = parser.parse_args()

    if not args.json_path.exists():
        print(f"File not found: {args.json_path}", file=sys.stderr)
        return 1

    regrets = load_regrets(args.json_path)
    created = 0
    skipped = 0
    for item in regrets:
        if not isinstance(item, dict):
            skipped += 1
            continue
        title = item.get("event_title") or "Рефлексия"
        quote = (
            item.get("source_quote")
            or item.get("consequences_lessons")
            or item.get("context")
            or title
        )
        if args.dry_run:
            print(f"  [dry-run] {title}")
            created += 1
            continue
        row = create_memory_candidate(
            candidate_type="regret",
            topic=title,
            content=item,
            source_type="note",
            source_id=str(args.json_path),
            source_quote=str(quote),
            confidence=float(item.get("confidence", 0.85)),
            metadata={
                "import": "regret_json",
                "origin": "manual_regret_import",
            },
        )
        if row:
            created += 1
            print(f"  + candidate id={row.get('id')} {title}")
        else:
            skipped += 1
            print(f"  skip duplicate {title}")

    print(f"Done: {created} created, {skipped} skipped.")
    print("Confirm via Telegram /review or Studio review queue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
