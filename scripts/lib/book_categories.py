"""Infer knowledge category from book filename or profile metadata."""

from __future__ import annotations

from pathlib import Path

ISLAM_KEYWORDS = ("quran", "koran", "islam", "ислам", "хадис", "фикх", "тафсир", "halal", "holy")
FINANCE_KEYWORDS = ("finance", "финанс", "халяль", "инвест", "money", "wealth")
IT_KEYWORDS = ("launch", "code", "program", "software", "developer", "api", "clean")
PSYCHOLOGY_KEYWORDS = ("hero", "outlaw", "archetype", "psych", "story", "persona", "brand")
BRANDING_KEYWORDS = ("work", "клеон", "brand", "marketing", "story")


def infer_category(source_name: str) -> str:
    lowered = source_name.lower()
    # Finance before islam so halal_finance_* maps to finance
    if any(keyword in lowered for keyword in FINANCE_KEYWORDS):
        return "finance"
    if any(keyword in lowered for keyword in ISLAM_KEYWORDS):
        return "islam"
    if any(keyword in lowered for keyword in IT_KEYWORDS):
        return "it"
    if any(keyword in lowered for keyword in PSYCHOLOGY_KEYWORDS):
        return "psychology"
    if any(keyword in lowered for keyword in BRANDING_KEYWORDS):
        return "branding"
    return "psychology"


def slugify_book_name(path: Path) -> str:
    stem = path.stem.lower()
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in stem)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")
