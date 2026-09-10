#!/usr/bin/env python3
"""Step 3 — Extract distilled JSON personality profiles from processed Markdown books."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.book_categories import infer_category, slugify_book_name  # noqa: E402
from scripts.lib.book_exclusions import exclusion_reason, is_excluded_book  # noqa: E402
from scripts.lib.cohere_client import CohereError, CohereRateLimitError, generate_json  # noqa: E402
from scripts.lib.md_header_chunker import chunk_markdown_by_headers  # noqa: E402
from scripts.lib.paths import BOOKS_PROCESSED, BOOKS_PROFILES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CHECKPOINT_DIR = BOOKS_PROFILES / ".checkpoints"
MAX_CHUNK_ATTEMPTS = int(os.getenv("COHERE_CHUNK_MAX_ATTEMPTS", "25"))

CHUNK_SYSTEM_PROMPT = """Ты — эксперт по лингвистическому анализу, психологии и брендингу.
Проанализируй кусок книги и извлеки только то, что явно следует из текста.
Верни строго JSON:
{
  "core_beliefs": ["..."],
  "style": {
    "tone_of_voice": "...",
    "speech_markers": ["..."],
    "forbidden_phrases": ["..."],
    "sentence_structure": "..."
  },
  "motivations": ["..."],
  "fears": ["..."],
  "forbidden_behaviors": ["..."]
}
Если данных нет — возвращай пустые строки и пустые массивы. Не выдумывай."""

AGGREGATION_SYSTEM_PROMPT = """Ты — архитектор личности ИИ (Knowledge Engineer).
Объедини промежуточные JSON-выжимки из разных частей одной книги в один финальный профиль.
Устрани дубликаты, сохрани только сильные и повторяющиеся паттерны.
Верни строго JSON в целевой структуре."""

FINAL_PROFILE_SCHEMA = {
    "profile_meta": {
        "source_book": "",
        "target_archetype_or_concept": "",
        "category": "",
        "processed_at": "",
    },
    "core_identity": {
        "mission": "",
        "beliefs": [],
        "fears": [],
    },
    "style_patterns": {
        "tone_of_voice": "",
        "speech_markers": [],
        "forbidden_phrases": [],
        "sentence_structure": "",
        "forbidden_behaviors": [],
    },
}


def _empty_chunk_result() -> dict[str, Any]:
    return {
        "core_beliefs": [],
        "style": {
            "tone_of_voice": "",
            "speech_markers": [],
            "forbidden_phrases": [],
            "sentence_structure": "",
        },
        "motivations": [],
        "fears": [],
        "forbidden_behaviors": [],
    }


def _merge_list_unique(base: list[str], new_items: list[str] | None) -> list[str]:
    if not new_items:
        return list(base)
    seen = {item.strip().lower() for item in base if item.strip()}
    merged = list(base)
    for item in new_items:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _checkpoint_path(md_path: Path) -> Path:
    return CHECKPOINT_DIR / f"{slugify_book_name(md_path)}_chunks.json"


def _load_checkpoint(md_path: Path) -> dict[int, dict[str, Any]]:
    path = _checkpoint_path(md_path)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {int(key): value for key, value in payload.get("chunks", {}).items()}


def _save_checkpoint(md_path: Path, chunks: dict[int, dict[str, Any]], total: int) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    serializable = {str(key): value for key, value in chunks.items()}
    _checkpoint_path(md_path).write_text(
        json.dumps({"total": total, "chunks": serializable}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_checkpoint(md_path: Path) -> None:
    path = _checkpoint_path(md_path)
    if path.exists():
        path.unlink()


def _chunk_has_data(chunk: dict[str, Any]) -> bool:
    if chunk.get("core_beliefs"):
        return True
    style = chunk.get("style", {})
    return bool(
        style.get("tone_of_voice")
        or style.get("speech_markers")
        or style.get("forbidden_phrases")
        or chunk.get("motivations")
        or chunk.get("fears")
    )


def extract_chunk(chunk: dict[str, str], *, sleep_seconds: float) -> dict[str, Any]:
    user_prompt = (
        f"Главы: {chunk.get('chapter_titles', 'unknown')}\n\n"
        f"{chunk.get('content', '')}"
    )
    for attempt in range(MAX_CHUNK_ATTEMPTS):
        try:
            result = generate_json(
                system_prompt=CHUNK_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=0.15,
            )
            time.sleep(sleep_seconds)
            return result
        except CohereRateLimitError:
            raise
        except (CohereError, json.JSONDecodeError) as exc:
            wait = min(sleep_seconds * (2**attempt), 120)
            logger.warning(
                "Chunk retry %s/%s after error: %s (sleep %.0fs)",
                attempt + 1,
                MAX_CHUNK_ATTEMPTS,
                exc,
                wait,
            )
            if attempt + 1 >= MAX_CHUNK_ATTEMPTS:
                raise
            time.sleep(wait)
    raise CohereError("Chunk extraction exhausted retries")


AGGREGATION_BATCH_SIZE = int(os.getenv("COHERE_AGGREGATION_BATCH_SIZE", "15"))
AGGREGATION_TIMEOUT = float(os.getenv("COHERE_AGGREGATION_TIMEOUT", "300"))

PROFILE_MERGE_SYSTEM_PROMPT = """Ты — архитектор личности ИИ (Knowledge Engineer).
Объедини несколько частичных профилей одной книги в один связный финальный профиль.
Дедуплицируй beliefs, fears и speech_markers. Синтезируй единую mission и tone_of_voice.
Сохрани только сильные, непротиворечивые паттерны. Не выдумывай.
Верни строго JSON в целевой структуре."""


def _cohere_aggregate_chunks(
    chunk_results: list[dict[str, Any]],
    *,
    book_name: str,
    category: str,
) -> dict[str, Any]:
    aggregation_input = json.dumps(chunk_results, ensure_ascii=False)
    user_prompt = f"""Книга: {book_name}
Категория: {category}

Промежуточные выжимки по чанкам:
{aggregation_input}

Собери финальный JSON строго в формате:
{json.dumps(FINAL_PROFILE_SCHEMA, ensure_ascii=False, indent=2)}
"""
    return generate_json(
        system_prompt=AGGREGATION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=AGGREGATION_TIMEOUT,
    )


def _cohere_merge_profiles(
    partial_profiles: list[dict[str, Any]],
    *,
    book_name: str,
    category: str,
) -> dict[str, Any]:
    merge_input = json.dumps(partial_profiles, ensure_ascii=False)
    user_prompt = f"""Книга: {book_name}
Категория: {category}

Частичные профили из разных частей книги:
{merge_input}

Собери один финальный профиль строго в формате:
{json.dumps(FINAL_PROFILE_SCHEMA, ensure_ascii=False, indent=2)}
"""
    return generate_json(
        system_prompt=PROFILE_MERGE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.1,
        timeout=AGGREGATION_TIMEOUT,
    )


def aggregate_profile(
    *,
    book_name: str,
    category: str,
    chunk_results: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        if len(chunk_results) <= AGGREGATION_BATCH_SIZE:
            return _cohere_aggregate_chunks(
                chunk_results, book_name=book_name, category=category
            )

        batches = [
            chunk_results[i : i + AGGREGATION_BATCH_SIZE]
            for i in range(0, len(chunk_results), AGGREGATION_BATCH_SIZE)
        ]
        partials: list[dict[str, Any]] = []
        for idx, batch in enumerate(batches, 1):
            logger.info(
                "Quality batch aggregation %s/%s for %s (%s chunks)",
                idx,
                len(batches),
                book_name,
                len(batch),
            )
            partials.append(
                _cohere_aggregate_chunks(batch, book_name=book_name, category=category)
            )

        while len(partials) > 1:
            if len(partials) <= AGGREGATION_BATCH_SIZE:
                logger.info(
                    "Merging %s partial profiles into final profile for %s",
                    len(partials),
                    book_name,
                )
                return _cohere_merge_profiles(
                    partials, book_name=book_name, category=category
                )
            next_level: list[dict[str, Any]] = []
            merge_groups = [
                partials[i : i + AGGREGATION_BATCH_SIZE]
                for i in range(0, len(partials), AGGREGATION_BATCH_SIZE)
            ]
            for group_idx, group in enumerate(merge_groups, 1):
                if len(group) == 1:
                    next_level.append(group[0])
                    continue
                logger.info(
                    "Quality merge tier %s/%s for %s (%s partials)",
                    group_idx,
                    len(merge_groups),
                    book_name,
                    len(group),
                )
                next_level.append(
                    _cohere_merge_profiles(group, book_name=book_name, category=category)
                )
            partials = next_level

        return partials[0]
    except CohereRateLimitError:
        raise
    except (CohereError, json.JSONDecodeError) as exc:
        logger.warning("Cohere aggregation failed, falling back to manual merge: %s", exc)
        return manual_aggregate(book_name=book_name, category=category, chunk_results=chunk_results)


def manual_aggregate(
    *,
    book_name: str,
    category: str,
    chunk_results: list[dict[str, Any]],
) -> dict[str, Any]:
    beliefs: list[str] = []
    fears: list[str] = []
    motivations: list[str] = []
    forbidden_behaviors: list[str] = []
    speech_markers: list[str] = []
    forbidden_phrases: list[str] = []
    tone_candidates: list[str] = []
    sentence_candidates: list[str] = []

    for chunk in chunk_results:
        beliefs = _merge_list_unique(beliefs, chunk.get("core_beliefs") or [])
        fears = _merge_list_unique(fears, chunk.get("fears") or [])
        motivations = _merge_list_unique(motivations, chunk.get("motivations") or [])
        forbidden_behaviors = _merge_list_unique(
            forbidden_behaviors, chunk.get("forbidden_behaviors") or []
        )
        style = chunk.get("style") or {}
        if style.get("tone_of_voice"):
            tone_candidates.append(style["tone_of_voice"])
        if style.get("sentence_structure"):
            sentence_candidates.append(style["sentence_structure"])
        speech_markers = _merge_list_unique(speech_markers, style.get("speech_markers") or [])
        forbidden_phrases = _merge_list_unique(
            forbidden_phrases, style.get("forbidden_phrases") or []
        )

    mission = motivations[0] if motivations else f"Концептуальная рамка из книги «{book_name}»."
    archetype = category if category != "psychology" else book_name

    return {
        "profile_meta": {
            "source_book": book_name,
            "target_archetype_or_concept": archetype,
            "category": category,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
        "core_identity": {
            "mission": mission,
            "beliefs": beliefs,
            "fears": fears,
        },
        "style_patterns": {
            "tone_of_voice": tone_candidates[0] if tone_candidates else "",
            "speech_markers": speech_markers,
            "forbidden_phrases": forbidden_phrases,
            "sentence_structure": sentence_candidates[0] if sentence_candidates else "",
            "forbidden_behaviors": forbidden_behaviors,
        },
    }


def process_markdown_file(
    md_path: Path,
    *,
    force: bool = False,
    max_tokens: int = 4000,
    overlap_ratio: float = 0.10,
    sleep_seconds: float = 2.0,
) -> dict[str, Any]:
    profile_path = BOOKS_PROFILES / f"{slugify_book_name(md_path)}.json"
    if is_excluded_book(md_path):
        return {
            "source": md_path.name,
            "profile": profile_path.name,
            "status": "excluded",
            "reason": exclusion_reason(md_path),
        }
    if profile_path.exists() and not force:
        return {
            "source": md_path.name,
            "profile": profile_path.name,
            "status": "skipped",
        }

    if force:
        _clear_checkpoint(md_path)

    markdown = md_path.read_text(encoding="utf-8")
    if not markdown.strip():
        return {
            "source": md_path.name,
            "profile": profile_path.name,
            "status": "error",
            "error": "empty_markdown",
        }

    book_name = md_path.stem.replace("_", " ")
    category = infer_category(md_path.name)
    chunks = chunk_markdown_by_headers(
        markdown,
        max_tokens=max_tokens,
        overlap_ratio=overlap_ratio,
    )

    checkpoint = _load_checkpoint(md_path)
    chunk_results: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        cached = checkpoint.get(index)
        if cached is not None and _chunk_has_data(cached):
            logger.info("Using checkpoint %s chunk %s/%s", md_path.name, index, len(chunks))
            chunk_results.append(cached)
            continue

        logger.info("Processing %s chunk %s/%s", md_path.name, index, len(chunks))
        result = extract_chunk(chunk, sleep_seconds=sleep_seconds)
        checkpoint[index] = result
        _save_checkpoint(md_path, checkpoint, len(chunks))
        chunk_results.append(result)

    profile = aggregate_profile(
        book_name=book_name,
        category=category,
        chunk_results=chunk_results,
    )
    profile.setdefault("profile_meta", {})
    profile["profile_meta"].setdefault("source_book", book_name)
    profile["profile_meta"].setdefault("category", category)
    profile["profile_meta"]["processed_at"] = datetime.now(timezone.utc).isoformat()

    BOOKS_PROFILES.mkdir(parents=True, exist_ok=True)
    with profile_path.open("w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)

    _clear_checkpoint(md_path)

    return {
        "source": md_path.name,
        "profile": profile_path.name,
        "status": "ok",
        "chunks": len(chunks),
        "beliefs": len(profile.get("core_identity", {}).get("beliefs", [])),
    }


def process_all_markdown(
    *,
    force: bool = False,
    max_tokens: int = 4000,
    overlap_ratio: float = 0.10,
    sleep_seconds: float = 2.0,
) -> dict[str, Any]:
    md_files = sorted(
        path for path in BOOKS_PROCESSED.glob("*.md") if not is_excluded_book(path)
    )
    if not md_files:
        raise FileNotFoundError(f"No markdown files found in {BOOKS_PROCESSED}")

    results = [
        process_markdown_file(
            path,
            force=force,
            max_tokens=max_tokens,
            overlap_ratio=overlap_ratio,
            sleep_seconds=sleep_seconds,
        )
        for path in md_files
    ]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "errors": sum(1 for item in results if item["status"] == "error"),
    }
    report_path = BOOKS_PROFILES / "extraction_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract book JSON profiles via Gemini.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--file", type=Path, default=None)
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--overlap", type=float, default=0.10)
    parser.add_argument("--sleep", type=float, default=2.0)
    args = parser.parse_args()

    if args.file:
        try:
            result = process_markdown_file(
                args.file,
                force=args.force,
                max_tokens=args.max_tokens,
                overlap_ratio=args.overlap,
                sleep_seconds=args.sleep,
            )
        except CohereRateLimitError as exc:
            logger.error("Stopped on rate limit: %s", exc)
            raise SystemExit(42) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "error":
            raise SystemExit(1)
        return

    try:
        report = process_all_markdown(
            force=args.force,
            max_tokens=args.max_tokens,
            overlap_ratio=args.overlap,
            sleep_seconds=args.sleep,
        )
    except CohereRateLimitError as exc:
        logger.error("Stopped on rate limit: %s", exc)
        raise SystemExit(42) from exc
    print("Book profile extraction complete.")
    print(f"  ok: {report['ok']}, skipped: {report['skipped']}, errors: {report['errors']}")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
