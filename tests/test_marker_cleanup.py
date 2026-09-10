"""Tests for Marker markdown post-processing."""

from __future__ import annotations

from scripts.lib.marker_cleanup import clean_marker_markdown


def test_removes_page_span_tags():
    raw = 'Введение<span id="page-7-0"></span> текст'
    cleaned = clean_marker_markdown(raw)
    assert "page-7-0" not in cleaned
    assert "Введение текст" in cleaned


def test_normalizes_h3_to_h2():
    raw = "### Глава 1\n\nТекст главы."
    cleaned = clean_marker_markdown(raw)
    assert cleaned.startswith("## Глава 1")


def test_merges_broken_lines_within_paragraph():
    raw = "Помогло ей и\n\nзнание основ коммуникационного эффекта."
    cleaned = clean_marker_markdown(raw)
    assert "Помогло ей и знание основ коммуникационного эффекта." in cleaned


def test_strips_escaped_footnote_page_anchors():
    raw = 'Текст [\\[1\\]](#page-254-0) продолжение.'
    cleaned = clean_marker_markdown(raw)
    assert "[1]" in cleaned
    assert "page-254-0" not in cleaned
    assert "[\\[1\\]](#page-254-0)" not in cleaned


def test_strips_return_to_page_links():
    raw = "Сноска. *Прим. ред.* [Вернуться](#page-7-0)"
    cleaned = clean_marker_markdown(raw)
    assert "Вернуться" not in cleaned
    assert "page-7-0" not in cleaned
    assert "Сноска. *Прим. ред.*" in cleaned
