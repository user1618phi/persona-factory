#!/usr/bin/env python3
"""Import cleaned Telegram posts/chats as style-only voice examples."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.project_runtime import ensure_project_venv  # noqa: E402


ensure_project_venv(__file__, ROOT)

from src.db.memory_repository import fingerprint  # noqa: E402
from src.db.supabase_client import get_supabase  # noqa: E402
from src.embeddings.gemini import embed_texts  # noqa: E402

DEFAULT_POSTS = ROOT / "data" / "cleaned" / "posts_clean.json"
DEFAULT_CHATS = ROOT / "data" / "cleaned" / "chats_clean.json"
logger = logging.getLogger(__name__)


def _load(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _existing_fingerprints() -> set[str]:
    client = get_supabase()
    fingerprints: set[str] = set()
    page_size = 1000
    offset = 0
    while True:
        response = (
            client.table("voice_examples")
            .select("fingerprint")
            .order("id")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = response.data or []
        fingerprints.update(
            row["fingerprint"] for row in rows if row.get("fingerprint")
        )
        if len(rows) < page_size:
            return fingerprints
        offset += page_size


def import_records(
    records: list[dict[str, Any]],
    *,
    source_type: str,
    quality_weight: float,
    batch_size: int,
    batch_sleep: float,
    rate_limit_wait: float,
    max_rate_limit_retries: int,
    wait_on_quota: bool,
    dry_run: bool,
) -> dict[str, Any]:
    existing = _existing_fingerprints()
    prepared: list[dict[str, Any]] = []
    seen = set(existing)
    for row in records:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        digest = fingerprint(source_type, text)
        if digest in seen:
            continue
        seen.add(digest)
        prepared.append(
            {
                "text": text,
                "source_type": source_type,
                "source_external_id": str(row.get("id") or ""),
                "source_chat": row.get("chat_name"),
                "occurred_at": row.get("date"),
                "quality_weight": quality_weight,
                "status": "active",
                "fingerprint": digest,
                "metadata": {
                    "chat_type": row.get("chat_type"),
                    "source_file": row.get("source_file"),
                    "style_only": source_type == "telegram_chat",
                },
            }
        )
    if dry_run:
        return {
            "remaining": len(prepared),
            "skipped_existing_or_duplicate": len(records) - len(prepared),
            "inserted": 0,
        }

    inserted = 0
    client = get_supabase()
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start : start + batch_size]
        batch_number = start // batch_size + 1
        total_batches = (len(prepared) + batch_size - 1) // batch_size
        retries = 0
        while True:
            try:
                vectors = embed_texts([row["text"] for row in batch])
                break
            except RuntimeError as exc:
                if "429" not in str(exc):
                    raise
                if not wait_on_quota:
                    remaining = len(prepared) - start
                    logger.error(
                        "Gemini embedding quota is exhausted. "
                        "Stopping safely before %s batch %s/%s; "
                        "%s records remain. Re-run later to resume.",
                        source_type,
                        batch_number,
                        total_batches,
                        remaining,
                    )
                    return {
                        "remaining": remaining,
                        "skipped_existing_or_duplicate": len(records) - len(prepared),
                        "inserted": inserted,
                        "status": "quota_exhausted",
                    }
                if retries >= max_rate_limit_retries:
                    return {
                        "remaining": len(prepared) - start,
                        "skipped_existing_or_duplicate": len(records) - len(prepared),
                        "inserted": inserted,
                        "status": "quota_exhausted_after_retries",
                    }
                retries += 1
                wait = rate_limit_wait * min(retries, 3)
                logger.warning(
                    "Gemini quota reached on %s batch %s/%s. "
                    "Progress is safe; retrying the same batch in %.0fs "
                    "(attempt %s/%s).",
                    source_type,
                    batch_number,
                    total_batches,
                    wait,
                    retries,
                    max_rate_limit_retries,
                )
                time.sleep(wait)
        payload = [
            {**row, "embedding": vector}
            for row, vector in zip(batch, vectors, strict=True)
        ]
        response = client.table("voice_examples").upsert(
            payload, on_conflict="fingerprint", ignore_duplicates=True
        ).execute()
        inserted += len(response.data or [])
        print(
            f"{source_type}: batch {batch_number}/{total_batches}, "
            f"inserted {inserted}/{len(prepared)}",
            flush=True,
        )
        if start + batch_size < len(prepared):
            time.sleep(batch_sleep)
    return {
        "remaining": 0,
        "skipped_existing_or_duplicate": len(records) - len(prepared),
        "inserted": inserted,
        "status": "complete",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Telegram voice examples.")
    parser.add_argument("--posts", type=Path, default=DEFAULT_POSTS)
    parser.add_argument("--chats", type=Path, default=DEFAULT_CHATS)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Texts per Gemini request. 20 is conservative for free-tier quotas.",
    )
    parser.add_argument(
        "--batch-sleep",
        type=float,
        default=15.0,
        help="Seconds between successful batches.",
    )
    parser.add_argument(
        "--rate-limit-wait",
        type=float,
        default=65.0,
        help="Base wait after HTTP 429; later retries use up to 3x this value.",
    )
    parser.add_argument(
        "--max-rate-limit-retries",
        type=int,
        default=20,
        help="Retries of the same batch before stopping. Reruns remain safe.",
    )
    parser.add_argument(
        "--wait-on-quota",
        action="store_true",
        help=(
            "Wait and retry after HTTP 429. Default behavior is to stop cleanly "
            "because simultaneous 429s on both keys usually mean daily quota exhaustion."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    posts = import_records(
        _load(args.posts),
        source_type="channel_post",
        quality_weight=1.0,
        batch_size=args.batch_size,
        batch_sleep=args.batch_sleep,
        rate_limit_wait=args.rate_limit_wait,
        max_rate_limit_retries=args.max_rate_limit_retries,
        wait_on_quota=args.wait_on_quota,
        dry_run=args.dry_run,
    )
    chats = import_records(
        _load(args.chats),
        source_type="telegram_chat",
        quality_weight=0.45,
        batch_size=args.batch_size,
        batch_sleep=args.batch_sleep,
        rate_limit_wait=args.rate_limit_wait,
        max_rate_limit_retries=args.max_rate_limit_retries,
        wait_on_quota=args.wait_on_quota,
        dry_run=args.dry_run,
    )
    report = {"posts": posts, "chats": chats}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.dry_run and any(
        item.get("status", "").startswith("quota_exhausted")
        for item in report.values()
    ):
        print(
            "\nИмпорт остановлен безопасно из-за Gemini quota. "
            "Повторите команду после сброса квоты; прогресс сохранён.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "\nИмпорт остановлен пользователем. Уже записанные batch сохранены; "
            "повторный запуск продолжит с оставшихся fingerprints.",
            file=sys.stderr,
        )
