"""Tests for category-aware text chunking."""

from __future__ import annotations

from scripts.lib.text_chunking import chunk_it_text, chunk_paragraph_text
from scripts.md_chunker import split_markdown_by_category


def test_chunk_it_text_sliding_window():
    text = "a" * 2500
    chunks = chunk_it_text(text, size=1000, overlap=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_paragraph_text_splits_on_blank_lines():
    text = "Первый абзац.\n\nВторой абзац.\n\nТретий."
    chunks = chunk_paragraph_text(text)
    assert chunks == ["Первый абзац.", "Второй абзац.", "Третий."]


def test_split_markdown_it_uses_sliding_window():
    text = "x" * 1500
    chunks = split_markdown_by_category(text, "it")
    assert len(chunks) >= 2


def test_split_markdown_islam_uses_paragraphs():
    text = "Аят о терпении.\n\nХадис о намерении."
    chunks = split_markdown_by_category(text, "islam")
    assert chunks == ["Аят о терпении.", "Хадис о намерении."]
