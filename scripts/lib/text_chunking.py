"""Category-aware text chunking for knowledge ingest."""

from __future__ import annotations


def chunk_it_text(text: str, *, size: int = 1000, overlap: int = 200) -> list[str]:
    """Sliding window for IT documentation (from deprecated ingest_knowledge.py)."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [chunk for chunk in chunks if chunk]


def chunk_paragraph_text(text: str) -> list[str]:
    """Split on blank lines — preserves islam/finance semantic boundaries."""
    return [part.strip() for part in text.split("\n\n") if part.strip()]
