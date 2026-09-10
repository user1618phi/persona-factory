#!/usr/bin/env python3
"""Step 4 — Semantic RAG chunking from books/processed/*.md into Supabase knowledge_chunks."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.book_categories import infer_category  # noqa: E402
from scripts.lib.text_chunking import chunk_it_text, chunk_paragraph_text  # noqa: E402
from scripts.lib.book_exclusions import exclusion_reason, is_excluded_book  # noqa: E402
from scripts.lib.paths import BOOKS_PROCESSED  # noqa: E402
from src.db.supabase_client import get_supabase  # noqa: E402
from src.embeddings.cohere import CohereEmbedRateLimitError, embed_text  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EMBED_BATCH_SLEEP = float(__import__("os").getenv("COHERE_EMBED_BATCH_SLEEP", "6.5"))


def _build_semantic_chunker():
    from langchain_experimental.text_splitter import SemanticChunker
    from langchain_huggingface import HuggingFaceEmbeddings

    model_name = __import__("os").getenv(
        "SEMANTIC_CHUNKER_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    embeddings = HuggingFaceEmbeddings(model_name=model_name)
    return SemanticChunker(
        embeddings,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=95,
    )


def _extract_chapter_title(chunk_text: str, *, max_length: int = 300) -> str:
    for line in chunk_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if len(title) > max_length:
                return title[: max_length - 3] + "..."
            return title
    return "General"


def split_markdown_by_category(markdown: str, category: str) -> list[str]:
    """Category-aware chunking: IT sliding window, islam/finance paragraphs, else semantic."""
    if category == "it":
        return chunk_it_text(markdown)
    if category in {"islam", "finance"}:
        return chunk_paragraph_text(markdown)
    return semantic_split_markdown(markdown)


def semantic_split_markdown(markdown: str) -> list[str]:
    splitter = _build_semantic_chunker()
    return [chunk.strip() for chunk in splitter.split_text(markdown) if chunk.strip()]


def _delete_existing_book_chunks(client, source_book: str) -> None:
    client.table("knowledge_chunks").delete().eq("source_book", source_book).execute()


def ingest_markdown_file(
    md_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    source_book = md_path.stem.replace("_", " ")
    category = infer_category(md_path.name)

    if is_excluded_book(md_path):
        return {
            "source": md_path.name,
            "status": "excluded",
            "reason": exclusion_reason(md_path),
        }

    if not dry_run and not force:
        client = get_supabase()
        existing = (
            client.table("knowledge_chunks")
            .select("id")
            .eq("source_book", source_book)
            .limit(1)
            .execute()
        )
        if existing.data:
            return {
                "source": md_path.name,
                "status": "skipped",
                "reason": "already_indexed",
            }

    markdown = md_path.read_text(encoding="utf-8")
    if not markdown.strip():
        return {
            "source": md_path.name,
            "status": "error",
            "error": "empty_markdown",
        }

    try:
        chunks = split_markdown_by_category(markdown, category)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Semantic chunking failed for %s", md_path.name)
        return {
            "source": md_path.name,
            "status": "error",
            "error": f"semantic_chunker: {exc}",
        }

    if dry_run:
        return {
            "source": md_path.name,
            "status": "dry_run",
            "chunks": len(chunks),
            "category": category,
        }

    client = get_supabase()
    if force:
        _delete_existing_book_chunks(client, source_book)

    inserted = 0
    for index, chunk in enumerate(chunks, start=1):
        try:
            client.table("knowledge_chunks").insert(
                {
                    "content": chunk,
                    "category": category,
                    "source_book": source_book,
                    "chapter_title": _extract_chapter_title(chunk),
                    "chunk_index": index,
                    "embedding": embed_text(chunk, input_type="search_document"),
                }
            ).execute()
            inserted += 1
            time.sleep(EMBED_BATCH_SLEEP)
        except CohereEmbedRateLimitError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to insert chunk %s from %s: %s", index, md_path.name, exc)

    return {
        "source": md_path.name,
        "status": "ok" if inserted else "error",
        "chunks_inserted": inserted,
        "category": category,
    }


def ingest_all_markdown(*, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    md_files = sorted(
        path for path in BOOKS_PROCESSED.glob("*.md") if not is_excluded_book(path)
    )
    if not md_files:
        raise FileNotFoundError(f"No markdown files found in {BOOKS_PROCESSED}")

    results = [ingest_markdown_file(path, force=force, dry_run=dry_run) for path in md_files]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "errors": sum(1 for item in results if item["status"] == "error"),
        "dry_run": dry_run,
    }

    report_path = BOOKS_PROCESSED / "rag_ingest_report.json"
    with report_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic RAG ingest from processed Markdown.")
    parser.add_argument("--force", action="store_true", help="Reindex even if book already exists")
    parser.add_argument("--dry-run", action="store_true", help="Only chunk, do not write to Supabase")
    parser.add_argument("--file", type=Path, default=None)
    args = parser.parse_args()

    if args.file:
        try:
            result = ingest_markdown_file(args.file, force=args.force, dry_run=args.dry_run)
        except CohereEmbedRateLimitError as exc:
            logger.error("Stopped on rate limit: %s", exc)
            raise SystemExit(42) from exc
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "error":
            raise SystemExit(1)
        return

    report = ingest_all_markdown(force=args.force, dry_run=args.dry_run)
    print("Semantic RAG ingest complete.")
    print(f"  ok: {report['ok']}, skipped: {report['skipped']}, errors: {report['errors']}")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
