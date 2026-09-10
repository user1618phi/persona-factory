"""Bounded V2.1 context assembly with strict memory-layer separation."""

from __future__ import annotations

import re
from typing import Any

from src.config import get_settings
from src.db.retrievers import (
    fetch_active_persona_rules,
    fetch_style_mode,
    retrieve_confirmed_experiences,
    retrieve_knowledge_v21,
    retrieve_voice_examples,
)

FACTUAL_QUERY_RE = re.compile(
    r"(халял|харам|фикх|аят|хадис|цитат|правил|коран|инвест|риба|"
    r"api|код|функци|алгоритм|термин|определени|исследован|статист|"
    r"налог|договор|закон|прибыл|выручк|рынок|модель|архитектур|"
    r"объясни|что такое|как работает|почему работает|источник)",
    re.IGNORECASE,
)
RELIGIOUS_QUERY_RE = re.compile(
    r"(ислам|аллах|коран|аят|хадис|сунн|халя[лль]|харам|фикх|риба|намаз|дуа)",
    re.IGNORECASE,
)


def needs_factual_knowledge(query: str, knowledge_rows: list[dict[str, Any]]) -> bool:
    """Gate external knowledge by explicit user need, not vector similarity alone."""
    return bool(FACTUAL_QUERY_RE.search(query))


def is_religious_query(query: str) -> bool:
    return bool(RELIGIOUS_QUERY_RE.search(query))


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_rules(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Подтверждённых правил пока нет."
    return "\n".join(
        f"- [{row.get('rule_type')}] {row.get('topic')}: "
        f"{_clip(str(row.get('content') or ''), 1200)}"
        for row in rows
    )


def _format_voice(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Релевантных примеров речи пока нет."
    return "\n\n".join(
        f"[пример {index}; источник={row.get('source_type')}]\n"
        f"{_clip(str(row.get('text') or ''), 1200)}"
        for index, row in enumerate(rows, start=1)
    )


def _format_experiences(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Подтверждённых релевантных историй пока нет."
    return "\n".join(
        f"- [{'REGRET' if row.get('is_regret') else 'EXPERIENCE'}] "
        f"{row.get('event_title')}: {row.get('context')} → "
        f"{row.get('choice_made')} → "
        f"{_clip(str(row.get('consequences_lessons') or ''), 1200)}"
        for row in rows
    )


def _format_knowledge(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Внешние знания не используются."
    return "\n".join(
        f"- [{row.get('canonical_source') or row.get('source_book')} / "
        f"{row.get('chapter_title') or 'General'} / "
        f"{row.get('verification_status')}] {_clip(str(row.get('content') or ''), 1800)}"
        for row in rows
    )


def build_personality_system_prompt(context: dict[str, str], *, religious: bool) -> str:
    religious_rule = (
        "Запрос касается религии. Не добавляй аят, хадис, норму или точную цитату, "
        "если её нет в VERIFIED KNOWLEDGE. Если verified-источника нет, редактируй "
        "только религиозные формулировки, уже данные автором."
        if religious
        else "Не добавляй религиозные цитаты или нормы без verified-источника."
    )
    return f"""Ты — редактор-двойник Мустафы и внутренний Core Identity Engine.

=== ПОДТВЕРЖДЁННЫЕ ФАКТЫ И ПРАВИЛА ===
{context["rules"]}

=== РЕАЛЬНЫЕ ПРИМЕРЫ ГОЛОСА ===
{context["voice"]}

=== ПОДТВЕРЖДЁННЫЙ ОПЫТ ===
{context["experiences"]}

=== VERIFIED / EXTERNAL KNOWLEDGE ===
{context["knowledge"]}

=== ВЫБРАННАЯ ТЕХНИКА ПОДАЧИ ===
{context["style_mode"]}

Правила:
- Пиши от первого лица, но не выдумывай факты, события, эмоции и намерения.
- Примеры голоса задают язык и ритм, но не являются источником биографии.
- Книжная техника меняет только подачу и не добавляет убеждения автора книги.
- Не упоминай книги и названия техник, если пользователь сам этого не просил.
- Не используй пафосные заявления о лидерстве, миссии или влиянии без прямого основания.
- Не повторяй одну и ту же мысль разными словами; если смысл уже сказан, сокращай.
- Не используй литературные обороты и эссеистику; пиши как в личном чате.
- {religious_rule}
"""


def build_context(query: str, *, style_mode: str = "my_voice") -> dict[str, Any]:
    settings = get_settings()
    religious = is_religious_query(query)

    rules = fetch_active_persona_rules(query=query, limit=settings.persona_rule_limit)
    voice = retrieve_voice_examples(query, limit=settings.voice_example_limit)
    experiences = retrieve_confirmed_experiences(
        query, limit=settings.experience_limit, include_regrets=True
    )
    should_use_knowledge = religious or needs_factual_knowledge(query, [])
    knowledge = (
        retrieve_knowledge_v21(
            query,
            limit=min(settings.knowledge_retrieval_limit, 3),
            verified_only=religious,
        )
        if should_use_knowledge
        else []
    )
    mode = fetch_style_mode(style_mode) or fetch_style_mode("my_voice") or {}

    formatted = {
        "rules": _format_rules(rules),
        "voice": _format_voice(voice),
        "experiences": _format_experiences(experiences),
        "knowledge": _format_knowledge(knowledge),
        "style_mode": str(mode.get("instruction") or "Только голос автора."),
    }
    system_prompt = build_personality_system_prompt(formatted, religious=religious)
    if len(system_prompt) > settings.context_max_chars:
        overflow = len(system_prompt) - settings.context_max_chars
        formatted["voice"] = _clip(
            formatted["voice"], max(1000, len(formatted["voice"]) - overflow)
        )
        system_prompt = build_personality_system_prompt(formatted, religious=religious)

    return {
        "formatted": formatted,
        "system_prompt": system_prompt,
        "meta": {
            "context_chars": len(system_prompt),
            "persona_rules_count": len(rules),
            "voice_examples_count": len(voice),
            "experiences_count": len(experiences),
            "knowledge_chunks_count": len(knowledge),
            "religious_guardrail": religious,
            "style_mode": mode.get("slug", "my_voice"),
        },
    }
