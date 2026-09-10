#!/usr/bin/env python3
"""
Phase 1.2 — Extract 5 personality layers from cleaned Telegram text via Gemini.

Full corpus (2447 messages):
  1. python scripts/data_cleaner.py --export-dir "Telegram Export" [--repair-json]
  2. python scripts/identity_extractor.py --min-score 15 --resume
     # or fresh run: --min-score 15 (default 25; use 15 for maximum coverage)
  3. On 503 errors, batches auto-retry with backoff; use --resume to continue.
  4. Progress saved after each batch to personality_profile.json + extraction_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

DEFAULT_INPUT = ROOT / "data" / "cleaned" / "all_clean.json"
DEFAULT_OUTPUT = ROOT / "data" / "extracted" / "personality_profile.json"
DEFAULT_BATCH_CHARS = 80_000
DEFAULT_MIN_SCORE = 25
REPORT_FILENAME = "extraction_report.json"

EXTRACTION_PROMPT = """Ты — аналитик цифровой личности. Из текста автора (Mustafa) извлеки ТОЛЬКО то, что явно следует из его слов.

Верни строго JSON без markdown:
{
  "identity": [{"topic": "...", "rules_and_values": "..."}],
  "beliefs": [{"topic": "...", "rules_and_values": "..."}],
  "decisions": [{
    "event_title": "...",
    "context": "...",
    "choice_made": "...",
    "consequences_lessons": "...",
    "event_timestamp": "YYYY-MM-DD или null",
    "is_regret": false
  }],
  "regrets": [{
    "event_title": "...",
    "context": "...",
    "choice_made": "...",
    "consequences_lessons": "...",
    "event_timestamp": "YYYY-MM-DD или null",
    "is_regret": true
  }],
  "style": {
    "stop_words": ["слова, которые автор избегает"],
    "tone_markers": ["признаки тона речи"],
    "syntax": ["паттерны построения фраз"]
  }
}

Правила:
- Не выдумывай факты. Если данных нет — верни пустые массивы.
- identity = долгосрочные цели и миссия (масштаб лет).
- beliefs = этика, ислам, финансы, подход к IT.
- decisions = переломные выборы в формате ситуация → выбор → итог.
- regrets = ошибки, выгорание, упущенные возможности.
- style = лингвистические маркеры из реальных фраз автора.
- Пиши на русском языке.

ТЕКСТ АВТОРА:
"""


def score_message(record: dict) -> int:
    score = min(record.get("char_count", 0) // 10, 200)
    if record.get("chat_type") == "public_channel":
        score += 100
    if record.get("char_count", 0) > 500:
        score += 50
    return score


def select_messages(records: list[dict], *, min_score: int) -> list[dict]:
    ranked = sorted(records, key=score_message, reverse=True)
    return [r for r in ranked if score_message(r) >= min_score]


def batch_messages(messages: list[dict], batch_chars: int) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_size = 0

    for message in messages:
        block = (
            f"[{message.get('date', '')} | {message.get('chat_name', '')}]\n"
            f"{message.get('text', '')}\n\n"
        )
        block_size = len(block)

        if current and current_size + block_size > batch_chars:
            batches.append(current)
            current = []
            current_size = 0

        current.append(message)
        current_size += block_size

    if current:
        batches.append(current)

    return batches


def build_batch_text(batch: list[dict]) -> str:
    parts: list[str] = []
    for message in batch:
        parts.append(
            f"[{message.get('date', '')} | {message.get('chat_name', '')}]\n"
            f"{message.get('text', '')}"
        )
    return "\n\n---\n\n".join(parts)


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _is_transient_http_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 500, 502, 503, 504}
    return False


def call_gemini(
    *,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float = 120.0,
    max_retries: int = 5,
) -> dict[str, Any]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "X-goog-api-key": api_key,
                    },
                    json=payload,
                )
                response.raise_for_status()

            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {data}")

            text = candidates[0]["content"]["parts"][0]["text"]
            return json.loads(strip_json_fence(text))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < max_retries - 1:
                delay = 10 * (attempt + 1) if _is_transient_http_error(exc) else 3 * (attempt + 1)
                time.sleep(delay)

    raise RuntimeError(f"Gemini call failed after retries: {last_error}")


def merge_topic_items(existing: list[dict], new_items: list[dict], key: str) -> list[dict]:
    index = {item.get(key, "").strip().lower(): item for item in existing}
    for item in new_items:
        topic = str(item.get(key, "")).strip()
        if not topic:
            continue
        normalized = topic.lower()
        if normalized in index:
            old = index[normalized]
            old_value = old.get("rules_and_values", "")
            new_value = item.get("rules_and_values", "")
            if new_value and new_value not in old_value:
                old["rules_and_values"] = f"{old_value}\n{new_value}".strip()
        else:
            index[normalized] = item
    return list(index.values())


def merge_decisions(existing: list[dict], new_items: list[dict]) -> list[dict]:
    index = {item.get("event_title", "").strip().lower(): item for item in existing}
    for item in new_items:
        title = str(item.get("event_title", "")).strip()
        if not title:
            continue
        normalized = title.lower()
        if normalized not in index:
            index[normalized] = item
    return list(index.values())


def merge_style(existing: dict, new_style: dict) -> dict:
    merged = {
        "stop_words": list(existing.get("stop_words", [])),
        "tone_markers": list(existing.get("tone_markers", [])),
        "syntax": list(existing.get("syntax", [])),
    }
    for key in merged:
        for value in new_style.get(key, []):
            if value and value not in merged[key]:
                merged[key].append(value)
    return merged


def empty_profile() -> dict[str, Any]:
    return {
        "identity": [],
        "beliefs": [],
        "decisions": [],
        "regrets": [],
        "style": {"stop_words": [], "tone_markers": [], "syntax": []},
    }


def merge_profiles(base: dict[str, Any], chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "identity": merge_topic_items(base["identity"], chunk.get("identity", []), "topic"),
        "beliefs": merge_topic_items(base["beliefs"], chunk.get("beliefs", []), "topic"),
        "decisions": merge_decisions(base["decisions"], chunk.get("decisions", [])),
        "regrets": merge_decisions(base["regrets"], chunk.get("regrets", [])),
        "style": merge_style(base["style"], chunk.get("style", {})),
    }


def load_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_profile()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    base = empty_profile()
    base.update({k: data.get(k, base[k]) for k in base})
    return base


def load_completed_batches(report_path: Path) -> set[int]:
    if not report_path.exists():
        return set()
    with report_path.open("r", encoding="utf-8") as handle:
        report = json.load(handle)
    return {
        item["batch"]
        for item in report.get("batches", [])
        if item.get("status") == "ok"
    }


def save_outputs(
    *,
    output_path: Path,
    profile: dict[str, Any],
    report: dict[str, Any],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)
    report_path = output_path.parent / REPORT_FILENAME
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)


def extract_personality(
    input_path: Path,
    output_path: Path,
    *,
    api_key: str,
    model: str,
    batch_chars: int,
    min_score: int,
    max_batches: int | None,
    sleep_seconds: float,
    resume: bool,
    start_batch: int,
) -> dict[str, Any]:
    with input_path.open("r", encoding="utf-8") as handle:
        records = json.load(handle)

    selected = select_messages(records, min_score=min_score)
    batches = batch_messages(selected, batch_chars)
    if max_batches is not None:
        batches = batches[:max_batches]

    report_path = output_path.parent / REPORT_FILENAME
    profile = load_profile(output_path) if resume else empty_profile()
    completed = load_completed_batches(report_path) if resume else set()

    batch_reports: list[dict[str, Any]] = []
    if resume and report_path.exists():
        with report_path.open("r", encoding="utf-8") as handle:
            prior = json.load(handle)
        batch_reports = prior.get("batches", [])

    for index, batch in enumerate(batches, start=1):
        if index < start_batch:
            continue
        if index in completed:
            print(f"Batch {index}/{len(batches)} — skipped (resume)")
            continue

        prompt = EXTRACTION_PROMPT + build_batch_text(batch)
        print(f"Batch {index}/{len(batches)} — {len(batch)} messages...")

        try:
            chunk = call_gemini(api_key=api_key, model=model, prompt=prompt)
            profile = merge_profiles(profile, chunk)
            entry = {
                "batch": index,
                "messages": len(batch),
                "status": "ok",
                "extracted": {
                    "identity": len(chunk.get("identity", [])),
                    "beliefs": len(chunk.get("beliefs", [])),
                    "decisions": len(chunk.get("decisions", [])),
                    "regrets": len(chunk.get("regrets", [])),
                },
            }
            batch_reports = [r for r in batch_reports if r.get("batch") != index]
            batch_reports.append(entry)
        except Exception as exc:  # noqa: BLE001
            entry = {
                "batch": index,
                "messages": len(batch),
                "status": "error",
                "error": str(exc),
            }
            batch_reports = [r for r in batch_reports if r.get("batch") != index]
            batch_reports.append(entry)
            print(f"  Error: {exc}")

        report = {
            "input": str(input_path),
            "selected_messages": len(selected),
            "total_messages": len(records),
            "min_score": min_score,
            "batches_total": len(batches),
            "batches": sorted(batch_reports, key=lambda item: item["batch"]),
            "final_counts": {
                "identity": len(profile["identity"]),
                "beliefs": len(profile["beliefs"]),
                "decisions": len(profile["decisions"]),
                "regrets": len(profile["regrets"]),
                "style_markers": sum(len(v) for v in profile["style"].values()),
            },
        }
        save_outputs(output_path=output_path, profile=profile, report=report)

        if index < len(batches):
            time.sleep(sleep_seconds)

    return profile


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract personality layers via Gemini.",
        epilog=(
            "Full corpus: data_cleaner.py first, then "
            "identity_extractor.py --min-score 15 --resume"
        ),
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-chars", type=int, default=DEFAULT_BATCH_CHARS)
    parser.add_argument(
        "--min-score",
        type=int,
        default=DEFAULT_MIN_SCORE,
        help="Message rank threshold (use 15 for full ~2447 corpus)",
    )
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-flash-latest"))
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue from existing profile and skip ok batches in extraction_report.json",
    )
    parser.add_argument(
        "--start-batch",
        type=int,
        default=1,
        help="1-based batch index to start from (for manual recovery)",
    )
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set in .env")

    if not args.input.exists():
        raise SystemExit(f"Input not found: {args.input}. Run data_cleaner.py first.")

    profile = extract_personality(
        args.input,
        args.output,
        api_key=api_key,
        model=args.model,
        batch_chars=args.batch_chars,
        min_score=args.min_score,
        max_batches=args.max_batches,
        sleep_seconds=args.sleep,
        resume=args.resume,
        start_batch=args.start_batch,
    )

    print("Extraction complete.")
    print(f"  identity: {len(profile['identity'])}")
    print(f"  beliefs: {len(profile['beliefs'])}")
    print(f"  decisions: {len(profile['decisions'])}")
    print(f"  regrets: {len(profile['regrets'])}")
    print(f"  Output: {args.output}")
    print(f"  Report: {args.output.parent / REPORT_FILENAME}")


if __name__ == "__main__":
    main()
