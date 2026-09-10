"""Post-processing cleanup for Marker-generated Markdown."""

from __future__ import annotations

import re

_PAGE_SPAN_RE = re.compile(r'<span[^>]*id="page-[^"]*"[^>]*>\s*</span>', re.IGNORECASE)
_EMPTY_SPAN_RE = re.compile(r"<span[^>]*>\s*</span>", re.IGNORECASE)
_PAGE_ANCHOR_LINK_RE = re.compile(r"\[(\d+)\]\(#page-\d+-\d+\)")
_ESCAPED_PAGE_ANCHOR_LINK_RE = re.compile(r"\[\\\[(\d+)\\\]\]\(#page-\d+-\d+\)")
_RETURN_TO_PAGE_LINK_RE = re.compile(r"\[Вернуться\]\(#page-\d+-\d+\)")
_H3_TO_H2_RE = re.compile(r"^###\s+", re.MULTILINE)
_SENTENCE_END_RE = re.compile(r"[.!?;:»\"')\]]$")
_LIST_START_RE = re.compile(r"^(\d+\.|[-*+])\s")


def _merge_broken_lines(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text

    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            if (
                merged
                and merged[-1].strip()
                and index + 1 < len(lines)
                and lines[index + 1].strip()
                and not merged[-1].strip().startswith("#")
                and not _SENTENCE_END_RE.search(merged[-1].rstrip())
            ):
                next_stripped = lines[index + 1].strip()
                if not next_stripped.startswith("#") and not _LIST_START_RE.match(next_stripped):
                    merged[-1] = f"{merged[-1].rstrip()} {next_stripped}"
                    index += 2
                    continue
            merged.append("")
            index += 1
            continue

        if (
            merged
            and merged[-1].strip()
            and not merged[-1].strip().startswith("#")
            and not _LIST_START_RE.match(stripped)
            and not stripped.startswith("#")
            and not _SENTENCE_END_RE.search(merged[-1].rstrip())
        ):
            merged[-1] = f"{merged[-1].rstrip()} {stripped}"
        else:
            merged.append(stripped)

        index += 1

    return "\n".join(merged)


def clean_marker_markdown(text: str) -> str:
    """Remove Marker artifacts and normalize headings for header-based chunking."""
    cleaned = _PAGE_SPAN_RE.sub("", text)
    cleaned = _EMPTY_SPAN_RE.sub("", cleaned)
    cleaned = _RETURN_TO_PAGE_LINK_RE.sub("", cleaned)
    cleaned = _ESCAPED_PAGE_ANCHOR_LINK_RE.sub(r"[\1]", cleaned)
    cleaned = _PAGE_ANCHOR_LINK_RE.sub(r"[\1]", cleaned)
    cleaned = _H3_TO_H2_RE.sub("## ", cleaned)
    cleaned = _merge_broken_lines(cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + "\n" if cleaned.strip() else ""
