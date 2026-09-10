#!/usr/bin/env python3
"""
DEPRECATED: use scripts/md_chunker.py instead.

Legacy PDF ingest kept for backward compatibility only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader  # noqa: E402

from src.db.supabase_client import get_supabase  # noqa: E402
from src.embeddings.gemini import embed_text  # noqa: E402

BOOKS_ROOT = ROOT / "data" / "raw" / "books"


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_pdf_file(path: Path) -> str:
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def chunk_it_text(text: str, *, size: int = 1000, overlap: int = 200) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [chunk for chunk in chunks if chunk]


def chunk_semantic_text(text: str) -> list[str]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    return paragraphs


def detect_category(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "islam" in parts:
        return "islam"
    if "finance" in parts:
        return "finance"
    return "it"


def ingest_file(path: Path) -> int:
    category = detect_category(path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        text = read_pdf_file(path)
    elif suffix in {".txt", ".md"}:
        text = read_text_file(path)
    else:
        return 0

    if category == "it":
        chunks = chunk_it_text(text)
    else:
        chunks = chunk_semantic_text(text)

    client = get_supabase()
    inserted = 0
    for chunk in chunks:
        client.table("knowledge_chunks").insert(
            {
                "content": chunk,
                "category": category,
                "embedding": embed_text(chunk),
            }
        ).execute()
        inserted += 1
    return inserted


def ingest_directory(books_root: Path) -> dict[str, int]:
    stats = {"files": 0, "chunks": 0}
    for path in sorted(books_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".pdf", ".txt", ".md"}:
            continue
        count = ingest_file(path)
        if count:
            stats["files"] += 1
            stats["chunks"] += count
            print(f"  {path.name}: {count} chunks")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest knowledge books into Supabase.")
    parser.add_argument("--books-dir", type=Path, default=BOOKS_ROOT)
    args = parser.parse_args()

    if not args.books_dir.exists():
        raise SystemExit(
            f"Books directory not found: {args.books_dir}\n"
            "Create subfolders: data/raw/books/islam, finance, it"
        )

    print(f"Ingesting from {args.books_dir}...")
    stats = ingest_directory(args.books_dir)
    print("Done:", stats)


if __name__ == "__main__":
    main()
