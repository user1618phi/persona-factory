#!/usr/bin/env python3
"""
Overnight books pipeline — runs every step end-to-end.

Stops ONLY on API rate limits (exit code 42). Resumes via overnight_state.json.
Log: books/processed/overnight_pipeline.log
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.book_categories import slugify_book_name  # noqa: E402
from scripts.lib.book_exclusions import is_excluded_book  # noqa: E402
from scripts.lib.paths import BOOKS_PROCESSED, BOOKS_PROFILES, BOOKS_RAW  # noqa: E402
from src.db.supabase_client import get_supabase  # noqa: E402

LOG_PATH = BOOKS_PROCESSED / "overnight_pipeline.log"
STATE_PATH = BOOKS_PROCESSED / "overnight_state.json"
PYTHON = sys.executable
SLEEP = "2"
RATE_LIMIT_EXIT = 42

STEPS = ("wait", "profiles", "rag", "seed", "convert", "new_books", "done")


def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("overnight")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = setup_logging()


def stamp(msg: str) -> None:
    log.info("=" * 60)
    log.info(msg)
    log.info("=" * 60)


def save_state(step: str) -> None:
    STATE_PATH.write_text(
        json.dumps(
            {"step": step, "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_state() -> str:
    if not STATE_PATH.exists():
        return STEPS[0]
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")).get("step", STEPS[0])
    except json.JSONDecodeError:
        return STEPS[0]


def wait_for_process(pattern: str, poll_seconds: int = 30) -> None:
    while True:
        result = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
        if result.returncode != 0:
            return
        log.info("Waiting for running process matching: %s", pattern)
        time.sleep(poll_seconds)


def run_script(script: str, extra: list[str] | None = None, step: str = "") -> None:
    cmd = [PYTHON, "-u", str(ROOT / "scripts" / script), *(extra or [])]
    stamp(f"STEP: {step or script}")
    log.info("Command: %s", " ".join(cmd))
    with LOG_PATH.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return_code = proc.wait()
    if return_code == RATE_LIMIT_EXIT:
        log.error("RATE LIMIT — pipeline paused at step: %s", step or script)
        raise SystemExit(RATE_LIMIT_EXIT)
    if return_code != 0:
        log.error("Step failed with exit %s: %s", return_code, step or script)
        raise SystemExit(return_code)


def list_markdown_books() -> list[Path]:
    return sorted(
        path for path in BOOKS_PROCESSED.glob("*.md") if not is_excluded_book(path)
    )


def profile_path_for(md_path: Path) -> Path:
    return BOOKS_PROFILES / f"{slugify_book_name(md_path)}.json"


def needs_profile_work(md_path: Path) -> bool:
    if checkpoint_incomplete(md_path):
        return True
    profile = profile_path_for(md_path)
    if not profile.exists():
        return True
    try:
        payload = json.loads(profile.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    beliefs = payload.get("core_identity", {}).get("beliefs", [])
    return len(beliefs) == 0


def checkpoint_incomplete(md_path: Path) -> bool:
    checkpoint = BOOKS_PROFILES / ".checkpoints" / f"{slugify_book_name(md_path)}_chunks.json"
    if not checkpoint.exists():
        return False
    try:
        data = json.loads(checkpoint.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return True
    total = int(data.get("total", 0))
    done = len(data.get("chunks", {}))
    return done < total


def extract_profiles() -> None:
    for md_path in list_markdown_books():
        if not needs_profile_work(md_path):
            log.info("Skip complete profile: %s", md_path.name)
            continue
        flags = ["--file", str(md_path), "--sleep", SLEEP]
        profile = profile_path_for(md_path)
        if profile.exists() and len(json.loads(profile.read_text()).get("core_identity", {}).get("beliefs", [])) == 0:
            flags.insert(0, "--force")
        run_script("book_processor.py", flags, step=f"Profile {md_path.name}")


def chunk_count_for_book(md_path: Path) -> int:
    source_book = md_path.stem.replace("_", " ")
    client = get_supabase()
    response = (
        client.table("knowledge_chunks")
        .select("id", count="exact")
        .eq("source_book", source_book)
        .execute()
    )
    return int(response.count or 0)


def ingest_rag(*, force: bool = False) -> None:
    for md_path in list_markdown_books():
        existing = chunk_count_for_book(md_path)
        slug = md_path.stem
        if slug == "627d32b823571642642689" and existing >= 30:
            log.info("Skip RAG (already indexed, %s chunks): %s", existing, md_path.name)
            continue
        flags = ["--file", str(md_path)]
        if existing > 0 and not force:
            log.info("Skip RAG (already indexed, %s chunks): %s", existing, md_path.name)
            continue
        if existing > 0:
            flags.insert(0, "--force")
        run_script("md_chunker.py", flags, step=f"RAG {md_path.name}")


def convert_pdfs() -> None:
    run_script("convert_pdf.py", step="Convert PDFs (Marker)")


def process_new_books() -> None:
    extract_profiles()
    for md_path in list_markdown_books():
        profile = profile_path_for(md_path)
        if not profile.exists():
            continue
        flags = ["--file", str(md_path)]
        run_script("md_chunker.py", flags, step=f"RAG new {md_path.name}")
    log.info("V2.1: book profiles remain offline and are not seeded into personality tables")


def write_summary() -> None:
    md_files = list_markdown_books()
    profiles = sorted(BOOKS_PROFILES.glob("*.json"))
    profiles = [p for p in profiles if p.name not in {"seed_report.json", "extraction_report.json"}]
    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "markdown_books": [p.name for p in md_files],
        "profiles": [p.name for p in profiles],
        "pdfs_raw": [p.name for p in sorted(BOOKS_RAW.glob("*.pdf")) if not is_excluded_book(p)],
    }
    out = BOOKS_PROCESSED / "overnight_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Summary written to %s", out)


def run_from(step: str) -> None:
    stamp(f"OVERNIGHT PIPELINE RESUME from '{step}' {datetime.now(timezone.utc).isoformat()}")
    start_index = STEPS.index(step) if step in STEPS else 0

    if start_index <= STEPS.index("wait"):
        save_state("wait")
        wait_for_process("book_processor.py")
        save_state("profiles")

    if start_index <= STEPS.index("profiles"):
        save_state("profiles")
        extract_profiles()
        save_state("rag")

    if start_index <= STEPS.index("rag"):
        save_state("rag")
        ingest_rag(force=True)
        save_state("seed")

    if start_index <= STEPS.index("seed"):
        save_state("seed")
        log.info("V2.1: skip legacy book-profile seeding")
        save_state("convert")

    if start_index <= STEPS.index("convert"):
        save_state("convert")
        convert_pdfs()
        save_state("new_books")

    if start_index <= STEPS.index("new_books"):
        save_state("new_books")
        process_new_books()
        save_state("done")

    write_summary()
    stamp(f"OVERNIGHT PIPELINE COMPLETE {datetime.now(timezone.utc).isoformat()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run overnight books pipeline with resume.")
    parser.add_argument(
        "--from-step",
        choices=list(STEPS),
        default=None,
        help="Resume from step (default: read overnight_state.json)",
    )
    args = parser.parse_args()
    step = args.from_step or load_state()
    if step == "done":
        log.info("Pipeline already marked done. Use --from-step rag to re-run.")
        return
    run_from(step)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code == RATE_LIMIT_EXIT:
            log.error("Stopped on rate limit. Re-run the same command to resume.")
        raise
