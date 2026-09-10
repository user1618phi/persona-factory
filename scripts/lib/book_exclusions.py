"""Books excluded from automated pipeline (too large or manual-only)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from scripts.lib.book_categories import slugify_book_name
from scripts.lib.paths import BOOKS_ROOT

EXCLUDED_FILE = BOOKS_ROOT / "excluded_books.json"

DEFAULT_EXCLUDED_PATTERNS = (
    "holy_quran_russian_compressed",
    "holy-quran",
    "holy_quran",
    "quran",
    "koran",
)


@lru_cache(maxsize=1)
def load_excluded_patterns() -> tuple[str, ...]:
    if EXCLUDED_FILE.exists():
        with EXCLUDED_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        patterns = payload.get("excluded_patterns", [])
        return tuple(str(item).lower() for item in patterns)
    return DEFAULT_EXCLUDED_PATTERNS


def is_excluded_book(path: Path) -> bool:
    slug = slugify_book_name(path).lower()
    name = path.name.lower()
    for pattern in load_excluded_patterns():
        if pattern in slug or pattern in name:
            return True
    return False


def exclusion_reason(path: Path) -> str:
    return f"excluded_by_policy:{slugify_book_name(path)}"
