#!/usr/bin/env python3
"""Create a deterministic, local-only holdout set from real channel posts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "cleaned" / "posts_clean.json"
DEFAULT_OUTPUT = ROOT / "data" / "benchmarks" / "voice_holdout.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build private voice benchmark.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=25)
    args = parser.parse_args()

    posts = json.loads(args.input.read_text(encoding="utf-8"))
    eligible = [row for row in posts if 80 <= len(str(row.get("text") or "")) <= 1500]
    if len(eligible) < args.count:
        raise SystemExit(f"Only {len(eligible)} eligible posts; requested {args.count}")
    step = len(eligible) / args.count
    selected = [eligible[int(index * step)] for index in range(args.count)]
    payload = [
        {
            "id": row.get("id"),
            "date": row.get("date"),
            "text": row.get("text"),
            "scores": {
                "voice_similarity": None,
                "clarity": None,
                "no_fabrication": None,
                "publish_ready": None,
                "length_match": None,
            },
        }
        for row in selected
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {len(payload)} holdout posts to {args.output}")


if __name__ == "__main__":
    main()
