#!/usr/bin/env python3
"""Phase 2.3 — Load personality profile JSON into Supabase."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.db.personality_writer import seed_profile_data  # noqa: E402
from src.db.supabase_client import get_supabase  # noqa: E402

DEFAULT_PROFILE = ROOT / "data" / "extracted" / "personality_profile.json"
DEFAULT_OVERRIDES = ROOT / "data" / "extracted" / "manual_overrides.json"
DEFAULT_REGRETS = ROOT / "data" / "extracted" / "regret_entries.json"
SEED_REPORT_PATH = ROOT / "data" / "extracted" / "seed_report.json"


def _load_json_list(path: Path | None, key: str) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return data
    return data.get(key, [])


def seed_profile(
    profile_path: Path,
    overrides_path: Path | None,
    regrets_path: Path | None,
) -> dict[str, Any]:
    with profile_path.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)

    for key in ("identity", "beliefs", "decisions", "regrets"):
        profile.setdefault(key, [])
        profile[key].extend(_load_json_list(overrides_path, key))

    regret_entries = _load_json_list(regrets_path, "regrets")
    profile["regrets"].extend(regret_entries)

    get_supabase()
    result = seed_profile_data(profile)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": str(profile_path),
        **result,
    }
    SEED_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SEED_REPORT_PATH.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase with personality profile.")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--regrets", type=Path, default=DEFAULT_REGRETS)
    args = parser.parse_args()

    raise SystemExit(
        "Disabled in V2.1: bulk seeding bypasses the mandatory review gate. "
        "Use Telegram /review and memory_candidates instead."
    )

    if not args.profile.exists():
        raise SystemExit(f"Profile not found: {args.profile}")

    report = seed_profile(args.profile, args.overrides, args.regrets)
    print("Seed complete.")
    print(f"  inserted: {report['inserted']}")
    print(f"  skipped (dedup): {report['skipped_duplicates']}")
    print(f"  report: {SEED_REPORT_PATH}")


if __name__ == "__main__":
    main()
