"""V2.1 invariants: separated memory, bounded context and explicit review."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from scripts.clean_knowledge import build_plan
from src.db.memory_repository import fingerprint
from src.orchestrator.brainstorm import (
    _generation_prompt,
    _postprocess_source_output,
    enforce_length,
    generate_draft,
    recommend_length,
)
from src.orchestrator.llm_router import GeneratedText
from src.orchestrator.context_builder import build_context
from src.orchestrator.learning import create_candidates_from_text
from src.orchestrator.publisher import publish_draft


def test_fingerprint_is_stable_across_whitespace_and_case():
    assert fingerprint("Style", "  Short   sentences ") == fingerprint(
        "style", "short sentences"
    )


def test_recommend_length_preserves_short_author_input():
    assert recommend_length("Одна короткая мысль.", intent="develop") == "short"
    assert recommend_length("x" * 400, intent="improve") == "normal"
    assert recommend_length("x" * 900, intent="improve") == "expanded"


def test_length_cap_never_cuts_a_short_post_beyond_mode_limit():
    text = ("Первое предложение. " * 30).strip()
    result = enforce_length(text, "short")
    assert len(result) <= 300
    assert result.endswith(".")


def test_youtube_generation_prompt_uses_takeaway_as_author_position():
    raw_input = """<!--source:youtube-->
<author_takeaway>
Мне стало понятнее, как душа проходит стадии развития.
</author_takeaway>
<video_grounded_notes>
Первый уровень связан с подчинением желаниям. [02:05]
</video_grounded_notes>
"""
    prompt = _generation_prompt(raw_input, intent="develop", length_mode="short")
    assert "ОБРАБОТКА YOUTUBE-ИСТОЧНИКА" in prompt
    assert "<author_takeaway> — это позиция" in prompt
    assert "Подкрепляй её фактами" in prompt
    assert "максимум 1 факт" in prompt
    assert "Не показывай XML-теги" in prompt


def test_youtube_generation_prompt_supports_personal_mode():
    raw_input = """<!--source:youtube-->
<youtube_post_mode>personal</youtube_post_mode>
URL: https://youtu.be/abc123
<author_takeaway>
Мне стало понятнее, как душа проходит стадии развития.
</author_takeaway>
<video_grounded_notes>
Первый уровень связан с подчинением желаниям. [02:05]
</video_grounded_notes>
"""
    prompt = _generation_prompt(raw_input, intent="develop", length_mode="normal")
    assert "РЕЖИМ ПОДАЧИ" in prompt
    assert "Не упоминай видео" in prompt
    assert "Не вставляй URL" in prompt
    assert "Не присваивай автору речь" in prompt
    assert "известный спикер" in prompt
    assert "Личный опыт добавляй только если" in prompt


def test_youtube_source_reference_postprocess_forces_url_at_end():
    raw_input = """<!--source:youtube-->
<youtube_post_mode>source_reference</youtube_post_mode>
URL: https://youtu.be/abc123
"""
    result = _postprocess_source_output(
        "Автор в видео хорошо разобрал тему. https://youtu.be/abc123 Спасибо.",
        raw_input,
    )
    assert result.endswith("https://youtu.be/abc123")
    assert result.count("https://youtu.be/abc123") == 1


def test_youtube_personal_postprocess_removes_url():
    raw_input = """<!--source:youtube-->
<youtube_post_mode>personal</youtube_post_mode>
URL: https://youtu.be/abc123
"""
    result = _postprocess_source_output(
        "Я понял важную мысль. https://youtu.be/abc123",
        raw_input,
    )
    assert "https://youtu.be/abc123" not in result


def test_single_draft_uses_one_llm_call_and_records_metrics(monkeypatch):
    monkeypatch.setattr(
        "src.orchestrator.brainstorm.build_context",
        lambda raw_input, style_mode: {
            "system_prompt": "system",
            "meta": {
                "context_chars": 6,
                "persona_rules_count": 1,
                "voice_examples_count": 2,
                "experiences_count": 0,
                "knowledge_chunks_count": 0,
            },
        },
    )
    llm = AsyncMock(
        return_value=GeneratedText(
            text="Готовый короткий текст.",
            route="gemini/test",
            model="gemini/test",
            duration_ms=100,
        )
    )
    monkeypatch.setattr("src.orchestrator.brainstorm.generate_text_with_meta", llm)
    recorded = []
    monkeypatch.setattr(
        "src.orchestrator.brainstorm.record_generation_run",
        lambda payload: recorded.append(payload),
    )

    async def _run():
        return await generate_draft(
            raw_input="идея",
            intent="develop",
            length_mode="short",
            style_mode="my_voice",
            draft_id="draft",
        )

    text, _ = asyncio.run(_run())
    assert text == "Готовый короткий текст."
    assert llm.await_count == 1
    assert recorded[0]["success"] is True


def test_context_uses_only_new_separated_sources(monkeypatch):
    monkeypatch.setattr(
        "src.orchestrator.context_builder.fetch_active_persona_rules",
        lambda query, limit: [
            {
                "rule_type": "belief",
                "topic": "Честность",
                "content": "Не выдумывать результаты.",
            }
        ],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_voice_examples",
        lambda query, limit: [{"text": "короткий реальный пример", "source_type": "channel_post"}],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_confirmed_experiences",
        lambda query, limit, include_regrets: [],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_knowledge_v21",
        lambda query, limit, verified_only: [],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.fetch_style_mode",
        lambda slug: {"slug": "my_voice", "instruction": "Только голос автора."},
    )
    result = build_context("короткий пост", style_mode="my_voice")
    assert "Не выдумывать результаты" in result["system_prompt"]
    assert "короткий реальный пример" in result["system_prompt"]
    assert result["meta"]["persona_rules_count"] == 1
    assert result["meta"]["context_chars"] < 30_000


def test_religious_context_requests_verified_knowledge(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "src.orchestrator.context_builder.fetch_active_persona_rules",
        lambda query, limit: [],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_voice_examples",
        lambda query, limit: [],
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_confirmed_experiences",
        lambda query, limit, include_regrets: [],
    )

    def _knowledge(query, limit, verified_only):
        captured["verified_only"] = verified_only
        return []

    monkeypatch.setattr(
        "src.orchestrator.context_builder.retrieve_knowledge_v21", _knowledge
    )
    monkeypatch.setattr(
        "src.orchestrator.context_builder.fetch_style_mode",
        lambda slug: {"slug": "my_voice", "instruction": "Только голос автора."},
    )
    result = build_context("что говорит ислам о долгах?")
    assert captured["verified_only"] is True
    assert result["meta"]["religious_guardrail"] is True


def test_chat_learning_can_be_style_only(monkeypatch):
    extracted = {
        "identity": [{"topic": "Не должно сохраниться", "source_quote": "пишу"}],
        "beliefs": [{"topic": "Не должно сохраниться", "source_quote": "пишу"}],
        "decisions": [{"event_title": "Не должно сохраниться", "source_quote": "пишу"}],
        "regrets": [],
        "style": {
            "tone_markers": [
                {"value": "коротко", "source_quote": "коротко", "confidence": 0.9}
            ],
            "stop_words": [],
            "syntax": [],
        },
    }
    monkeypatch.setattr(
        "src.orchestrator.learning.extract_from_note",
        AsyncMock(return_value=extracted),
    )
    created_types = []

    def _create(**kwargs):
        created_types.append(kwargs["candidate_type"])
        return kwargs

    monkeypatch.setattr("src.orchestrator.learning.create_memory_candidate", _create)

    async def _run():
        return await create_candidates_from_text(
            "пишу коротко",
            source_type="note",
            allow_facts=False,
        )

    asyncio.run(_run())
    assert created_types == ["style"]


def test_knowledge_cleanup_plans_dedup_merge_and_split():
    rows = [
        {
            "id": 1,
            "content": "duplicate text " * 10,
            "source_book": "book",
            "category": "it",
            "chapter_title": "A",
            "chunk_index": 1,
            "metadata": {},
            "verification_status": "unverified",
        },
        {
            "id": 2,
            "content": "duplicate text " * 10,
            "source_book": "book",
            "category": "it",
            "chapter_title": "A",
            "chunk_index": 2,
            "metadata": {},
            "verification_status": "unverified",
        },
        {
            "id": 3,
            "content": "short",
            "source_book": "book",
            "category": "it",
            "chapter_title": "A",
            "chunk_index": 3,
            "metadata": {},
            "verification_status": "unverified",
        },
        {
            "id": 4,
            "content": ("long paragraph.\n\n" * 500),
            "source_book": "book",
            "category": "it",
            "chapter_title": "A",
            "chunk_index": 4,
            "metadata": {},
            "verification_status": "unverified",
        },
    ]
    plan = build_plan(rows)
    assert plan["stats"]["duplicate_groups"] == 1
    assert plan["stats"]["merged_short"] == 1
    assert plan["stats"]["split_long"] == 1
    assert plan["inserts"]
    assert all("embedding" not in row for row in plan["updates"])


def test_publish_is_idempotent_when_token_already_recorded(monkeypatch):
    bot = MagicMock()
    bot.send_message = AsyncMock()
    monkeypatch.setattr(
        "src.orchestrator.publisher.get_draft",
        lambda draft_id: {
            "id": draft_id,
            "state": "published",
            "publish_token": "token",
            "final_text": "text",
        },
    )
    monkeypatch.setattr(
        "src.orchestrator.publisher._published_by_token",
        lambda token: {"message_id": 42},
    )

    async def _run():
        return await publish_draft(bot, "draft-id")

    result = asyncio.run(_run())
    assert result == (42, False)
    bot.send_message.assert_not_awaited()
