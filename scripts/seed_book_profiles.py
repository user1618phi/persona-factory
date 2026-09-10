#!/usr/bin/env python3
"""Book profiles are offline artifacts; V2.1 never seeds them into author identity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.paths import BOOKS_PROFILES  # noqa: E402


def _belief_topic(source_book: str, index: int) -> str:
    """Unique topic per book belief so conflict_resolver does not collapse rows."""
    return f"{source_book}: убеждение #{index}"


def seed_profile_file(profile_path: Path, *, force: bool = False) -> dict[str, Any]:
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    meta = profile.get("profile_meta", {})
    source_book = str(meta.get("source_book", profile_path.stem))
    return {
        "profile": profile_path.name,
        "status": "skipped",
        "source_book": source_book,
        "reason": (
            "v2.1 keeps book profiles out of persona_rules and voice_examples; "
            "selected techniques live in style_modes"
        ),
    }


def seed_all_profiles(*, force: bool = False) -> dict[str, Any]:
    profile_files = sorted(
        path
        for path in BOOKS_PROFILES.glob("*.json")
        if path.name not in {"extraction_report.json", "seed_report.json"}
    )
    if not profile_files:
        raise FileNotFoundError(f"No profile JSON files found in {BOOKS_PROFILES}")

    results = [seed_profile_file(path, force=force) for path in profile_files]
    report = {
        "results": results,
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "errors": sum(1 for item in results if item.get("status") == "error"),
    }

    report_path = BOOKS_PROFILES / "seed_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed book profiles into Supabase.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--file", type=Path, default=None)
    args = parser.parse_args()

    if args.file:
        result = seed_profile_file(args.file, force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "error":
            raise SystemExit(1)
        return

    report = seed_all_profiles(force=args.force)
    print("Book profile seeding complete.")
    print(f"  ok: {report['ok']}, skipped: {report['skipped']}, errors: {report['errors']}")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
