#!/usr/bin/env python3
"""
Phase 1.1 — Clean Telegram exports into structured JSON for identity extraction.

Reads all result.json files under Telegram Export/, keeps only the author's
meaningful text (posts + personal messages), and strips media/service noise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.telegram_text import (  # noqa: E402
    flatten_telegram_text,
    is_noise_message,
    iter_chat_sources,
    iter_export_files,
    load_json,
)


DEFAULT_EXPORT_DIR = ROOT / "Telegram Export"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "cleaned"
DEFAULT_USER_FROM_ID = "user123456789"


def message_author_id(message: dict) -> str:
    return str(message.get("from_id") or message.get("actor_id") or "")


def is_author_message(
    message: dict,
    *,
    user_from_id: str,
    chat_type: str,
) -> bool:
    if message.get("type") != "message":
        return False

    author_id = message_author_id(message)
    if author_id == user_from_id:
        return True

    # Channel posts authored by the user's public channels
    if chat_type == "public_channel" and author_id.startswith("channel"):
        return True

    return False


def has_media_only(message: dict) -> bool:
    media_keys = (
        "photo",
        "file",
        "video",
        "animation",
        "sticker",
        "voice",
        "video_message",
        "audio",
    )
    text = flatten_telegram_text(message.get("text"))
    return not text and any(key in message for key in media_keys)


def normalize_record(
    message: dict,
    *,
    chat_name: str,
    chat_type: str,
    source_file: str,
) -> dict | None:
    if has_media_only(message):
        return None

    text = flatten_telegram_text(message.get("text"))
    if is_noise_message(text):
        return None

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "id": message.get("id"),
        "date": message.get("date"),
        "chat_name": chat_name,
        "chat_type": chat_type,
        "source_file": source_file,
        "text": text,
        "content_hash": content_hash,
        "char_count": len(text),
    }


def deduplicate(records: list[dict]) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for record in records:
        content_hash = record["content_hash"]
        if content_hash in seen:
            continue
        seen.add(content_hash)
        unique.append(record)
    return unique


def split_outputs(records: list[dict]) -> tuple[list[dict], list[dict]]:
    posts = [r for r in records if r["chat_type"] == "public_channel"]
    chats = [r for r in records if r["chat_type"] != "public_channel"]
    return posts, chats


def build_stats(records: list[dict]) -> dict:
    by_chat: dict[str, int] = defaultdict(int)
    total_chars = 0
    for record in records:
        by_chat[record["chat_name"]] += 1
        total_chars += record["char_count"]

    top_chats = sorted(by_chat.items(), key=lambda item: item[1], reverse=True)[:15]
    return {
        "message_count": len(records),
        "total_characters": total_chars,
        "top_chats": [{"chat": name, "messages": count} for name, count in top_chats],
    }


def clean_exports(
    export_dir: Path,
    output_dir: Path,
    *,
    user_from_id: str,
    repair_json: bool = False,
) -> dict:
    all_records: list[dict] = []

    skipped_files: list[str] = []

    for export_file in iter_export_files(export_dir):
        try:
            payload = load_json(export_file, repair=repair_json)
        except ValueError as exc:
            skipped_files.append(f"{export_file.name}: {exc}")
            continue

        source_name = str(export_file.relative_to(export_dir))

        for chat in iter_chat_sources(payload, source_name):
            chat_type = str(chat.get("type", "unknown"))
            chat_name = str(chat.get("name", "unknown"))

            for message in chat.get("messages", []):
                if not is_author_message(
                    message,
                    user_from_id=user_from_id,
                    chat_type=chat_type,
                ):
                    continue

                record = normalize_record(
                    message,
                    chat_name=chat_name,
                    chat_type=chat_type,
                    source_file=source_name,
                )
                if record:
                    all_records.append(record)

    all_records = deduplicate(all_records)
    posts, chats = split_outputs(all_records)

    output_dir.mkdir(parents=True, exist_ok=True)

    posts_path = output_dir / "posts_clean.json"
    chats_path = output_dir / "chats_clean.json"
    combined_path = output_dir / "all_clean.json"
    stats_path = output_dir / "cleaning_stats.json"

    for path, payload in (
        (posts_path, posts),
        (chats_path, chats),
        (combined_path, all_records),
    ):
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "export_dir": str(export_dir),
        "user_from_id": user_from_id,
        "skipped_files": skipped_files,
        "posts": build_stats(posts),
        "chats": build_stats(chats),
        "combined": build_stats(all_records),
    }

    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, ensure_ascii=False, indent=2)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean Telegram export JSON files.")
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=DEFAULT_EXPORT_DIR,
        help="Path to Telegram Export folder",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for cleaned JSON",
    )
    parser.add_argument(
        "--user-from-id",
        default=DEFAULT_USER_FROM_ID,
        help="Telegram from_id of the author (e.g. user123456789)",
    )
    parser.add_argument(
        "--repair-json",
        action="store_true",
        help="Attempt best-effort repair of broken result.json exports",
    )
    args = parser.parse_args()

    if not args.export_dir.exists():
        raise SystemExit(f"Export directory not found: {args.export_dir}")

    stats = clean_exports(
        args.export_dir,
        args.output_dir,
        user_from_id=args.user_from_id,
        repair_json=args.repair_json,
    )

    combined = stats["combined"]
    print("Cleaning complete.")
    print(f"  Messages kept: {combined['message_count']}")
    print(f"  Total characters: {combined['total_characters']}")
    print(f"  Posts: {stats['posts']['message_count']}")
    print(f"  Chats: {stats['chats']['message_count']}")
    print(f"  Output: {args.output_dir}")
    if stats.get("skipped_files"):
        print(f"  Skipped/failed exports: {len(stats['skipped_files'])}")
        for item in stats["skipped_files"][:5]:
            print(f"    - {item}")


if __name__ == "__main__":
    main()
