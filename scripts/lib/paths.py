"""Project and books directory paths."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOKS_ROOT = ROOT / "books"
BOOKS_RAW = BOOKS_ROOT / "raw"
BOOKS_PROCESSED = BOOKS_ROOT / "processed"
BOOKS_PROFILES = BOOKS_ROOT / "profiles"
