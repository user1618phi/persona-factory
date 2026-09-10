"""Markdown section chunking for book_processor (header-based, token-limited)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.lib.token_estimator import estimate_tokens

SECTION_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
MAX_TOKENS_DEFAULT = 4000
OVERLAP_RATIO_DEFAULT = 0.10


@dataclass
class MarkdownSection:
    level: int
    title: str
    body: str

    @property
    def text(self) -> str:
        prefix = "#" * self.level
        return f"{prefix} {self.title}\n\n{self.body}".strip()


def parse_markdown_sections(markdown: str) -> list[MarkdownSection]:
    matches = list(SECTION_RE.finditer(markdown))
    if not matches:
        return [MarkdownSection(level=1, title="Document", body=markdown.strip())]

    sections: list[MarkdownSection] = []
    preamble = markdown[: matches[0].start()].strip()
    if preamble:
        sections.append(MarkdownSection(level=1, title="Preamble", body=preamble))

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections.append(
            MarkdownSection(
                level=len(match.group(1)),
                title=match.group(2).strip(),
                body=body,
            )
        )
    return sections


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    if overlap_tokens <= 0:
        return ""
    words = text.split()
    approx_words = max(overlap_tokens * 2, 40)
    return " ".join(words[-approx_words:])


def chunk_markdown_by_headers(
    markdown: str,
    *,
    max_tokens: int = MAX_TOKENS_DEFAULT,
    overlap_ratio: float = OVERLAP_RATIO_DEFAULT,
) -> list[dict[str, str]]:
    sections = parse_markdown_sections(markdown)
    overlap_tokens = int(max_tokens * overlap_ratio)

    chunks: list[dict[str, str]] = []
    buffer_parts: list[str] = []
    buffer_titles: list[str] = []
    buffer_tokens = 0
    previous_tail = ""

    def flush() -> None:
        nonlocal buffer_parts, buffer_titles, buffer_tokens, previous_tail
        if not buffer_parts:
            return
        content = "\n\n".join(buffer_parts).strip()
        if previous_tail and overlap_tokens:
            content = f"{previous_tail}\n\n{content}"
        chunks.append(
            {
                "chapter_titles": " | ".join(buffer_titles),
                "content": content,
            }
        )
        previous_tail = _overlap_tail(content, overlap_tokens)
        buffer_parts = []
        buffer_titles = []
        buffer_tokens = 0

    for section in sections:
        section_text = section.text
        section_tokens = estimate_tokens(section_text)

        if section_tokens > max_tokens:
            flush()
            paragraphs = [p.strip() for p in section.body.split("\n\n") if p.strip()]
            part_buffer: list[str] = []
            part_tokens = estimate_tokens(f"{' '.join(part_buffer)}")
            for paragraph in paragraphs:
                paragraph_block = f"## {section.title}\n\n{paragraph}"
                paragraph_tokens = estimate_tokens(paragraph_block)
                if part_buffer and part_tokens + paragraph_tokens > max_tokens:
                    content = "\n\n".join(part_buffer)
                    if previous_tail and overlap_tokens:
                        content = f"{previous_tail}\n\n{content}"
                    chunks.append(
                        {
                            "chapter_titles": section.title,
                            "content": content,
                        }
                    )
                    previous_tail = _overlap_tail(content, overlap_tokens)
                    part_buffer = []
                    part_tokens = 0
                part_buffer.append(paragraph_block)
                part_tokens += paragraph_tokens
            if part_buffer:
                content = "\n\n".join(part_buffer)
                if previous_tail and overlap_tokens:
                    content = f"{previous_tail}\n\n{content}"
                chunks.append(
                    {
                        "chapter_titles": section.title,
                        "content": content,
                    }
                )
                previous_tail = _overlap_tail(content, overlap_tokens)
            continue

        if buffer_parts and buffer_tokens + section_tokens > max_tokens:
            flush()

        buffer_parts.append(section_text)
        buffer_titles.append(section.title)
        buffer_tokens += section_tokens

    flush()
    return chunks
