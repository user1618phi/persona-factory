"""Authenticated HTTP API for the Next.js studio."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.config import get_settings
from src.db.memory_repository import (
    accept_review_item,
    create_memory_candidate,
    edit_review_item,
    fetch_knowledge_review_queue,
    next_review_item,
    reject_review_item,
    skip_review_item,
    update_knowledge_verification,
)
from src.db.supabase_client import get_supabase
from src.orchestrator.llm_router import LLMRouterError, generate_text

router = APIRouter(prefix="/api/v1", tags=["studio"])


def _verify_studio_secret(
    x_studio_secret: str | None = Header(default=None, alias="X-Studio-Secret"),
) -> None:
    expected = os.getenv("STUDIO_API_SECRET", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="STUDIO_API_SECRET is not configured on the server",
        )
    if x_studio_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid studio API secret")


class ReviewActionBody(BaseModel):
    kind: str = Field(..., pattern="^(candidate|rule|experience)$")
    record_id: int


class ReviewEditBody(ReviewActionBody):
    text: str = Field(..., min_length=1)


class InterviewContextBody(BaseModel):
    journal_entries: list[dict[str, Any]] = Field(default_factory=list)
    persona_rules: list[dict[str, Any]] = Field(default_factory=list)


class InterviewAnswerBody(BaseModel):
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)
    create_candidate: bool = True


class KnowledgeVerifyBody(BaseModel):
    ids: list[int] = Field(..., min_length=1)
    status: str = Field(..., pattern="^(verified|rejected|unverified)$")


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


async def _generate_json(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    try:
        text = await generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,
        )
    except LLMRouterError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        return _parse_json_response(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail="LLM вернул невалидный JSON",
        ) from exc


def _count_rows(table: str, *, filters: dict[str, Any] | None = None) -> int:
    query = get_supabase().table(table).select("id", count="exact")
    if filters:
        for key, value in filters.items():
            if isinstance(value, list):
                query = query.in_(key, value)
            else:
                query = query.eq(key, value)
    response = query.limit(1).execute()
    return int(response.count or 0)


def _fetch_pending_review_queue(limit: int = 50) -> list[dict[str, Any]]:
    client = get_supabase()
    queue: list[dict[str, Any]] = []

    for kind, table in (
        ("candidate", "memory_candidates"),
        ("rule", "persona_rules"),
        ("experience", "life_experiences"),
    ):
        response = (
            client.table(table)
            .select("*")
            .eq("status", "pending")
            .order("created_at")
            .limit(limit)
            .execute()
        )
        for row in response.data or []:
            queue.append({"kind": kind, "record": row})

    queue.sort(key=lambda item: item["record"].get("created_at") or "")
    return queue[:limit]


@router.get("/stats", dependencies=[Depends(_verify_studio_secret)])
async def studio_stats() -> dict[str, int]:
    def _collect() -> dict[str, int]:
        pending_candidates = _count_rows("memory_candidates", filters={"status": "pending"})
        pending_rules = _count_rows("persona_rules", filters={"status": "pending"})
        pending_experiences = _count_rows("life_experiences", filters={"status": "pending"})
        return {
            "active_rules": _count_rows("persona_rules", filters={"status": "active"}),
            "active_experiences": _count_rows(
                "life_experiences", filters={"status": "active"}
            ),
            "pending_review": pending_candidates + pending_rules + pending_experiences,
            "voice_examples": _count_rows("voice_examples", filters={"status": "active"}),
            "verified_knowledge": _count_rows(
                "knowledge_chunks", filters={"verification_status": "verified"}
            ),
            "unverified_knowledge": _count_rows(
                "knowledge_chunks", filters={"verification_status": "unverified"}
            ),
            "islam_unverified": _count_rows(
                "knowledge_chunks",
                filters={
                    "category": "islam",
                    "verification_status": "unverified",
                },
            ),
        }

    return await asyncio.to_thread(_collect)


@router.get("/review/next", dependencies=[Depends(_verify_studio_secret)])
async def review_next() -> dict[str, Any] | None:
    item = await asyncio.to_thread(next_review_item)
    return item


@router.get("/review/queue", dependencies=[Depends(_verify_studio_secret)])
async def review_queue(limit: int = 50) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_pending_review_queue, limit)


@router.post("/review/accept", dependencies=[Depends(_verify_studio_secret)])
async def review_accept(body: ReviewActionBody) -> dict[str, str]:
    user_id = get_settings().my_user_id
    if not user_id:
        raise HTTPException(status_code=500, detail="MY_USER_ID is not configured")

    status = await asyncio.to_thread(
        accept_review_item, body.kind, body.record_id, user_id
    )
    return {"status": status if isinstance(status, str) else "accepted"}


@router.post("/review/reject", dependencies=[Depends(_verify_studio_secret)])
async def review_reject(body: ReviewActionBody) -> dict[str, str]:
    user_id = get_settings().my_user_id
    if not user_id:
        raise HTTPException(status_code=500, detail="MY_USER_ID is not configured")

    await asyncio.to_thread(
        reject_review_item, body.kind, body.record_id, user_id
    )
    return {"status": "rejected"}


@router.post("/review/skip", dependencies=[Depends(_verify_studio_secret)])
async def review_skip(body: ReviewActionBody) -> dict[str, str]:
    await asyncio.to_thread(skip_review_item, body.kind, body.record_id)
    return {"status": "skipped"}


@router.post("/review/edit", dependencies=[Depends(_verify_studio_secret)])
async def review_edit(body: ReviewEditBody) -> dict[str, str]:
    await asyncio.to_thread(
        edit_review_item, body.kind, body.record_id, body.text
    )
    return {"status": "edited"}


@router.get("/knowledge/chunks", dependencies=[Depends(_verify_studio_secret)])
async def knowledge_chunks(
    category: str = "islam",
    status: str = "unverified",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status not in {"verified", "rejected", "unverified"}:
        raise HTTPException(status_code=400, detail="Invalid verification status")
    return await asyncio.to_thread(
        fetch_knowledge_review_queue,
        category=category,
        status=status,
        limit=limit,
    )


@router.post("/knowledge/verify", dependencies=[Depends(_verify_studio_secret)])
async def knowledge_verify(body: KnowledgeVerifyBody) -> dict[str, int | str]:
    updated = await asyncio.to_thread(
        update_knowledge_verification,
        ids=body.ids,
        status=body.status,
    )
    return {"status": body.status, "updated": updated}


@router.post("/interview/generate-question", dependencies=[Depends(_verify_studio_secret)])
async def interview_generate_question(body: InterviewContextBody) -> dict[str, Any]:
    context = json.dumps(
        {
            "journal_entries": body.journal_entries,
            "persona_rules": body.persona_rules,
        },
        ensure_ascii=False,
    )
    system_prompt = (
        "Ты ИИ-двойник Мустафы. Отвечай только валидным JSON без markdown и комментариев."
    )
    user_prompt = f"""
Проанализируй контекст и задай ОДИН глубокий вопрос на русском.
Формат: multiple choice с 3-4 вариантами. Автор также может ответить своим текстом.

Контекст:
{context}

Верни СТРОГО JSON:
{{
  "question": "вопрос",
  "options": ["Вариант 1", "Вариант 2", "Вариант 3"]
}}
"""
    parsed = await _generate_json(system_prompt, user_prompt)
    if not parsed.get("question") or not isinstance(parsed.get("options"), list):
        raise HTTPException(status_code=502, detail="Invalid interview question shape")
    return {
        "question": parsed["question"],
        "options": parsed["options"],
    }


@router.post("/interview/extract-answer", dependencies=[Depends(_verify_studio_secret)])
async def interview_extract_answer(body: InterviewAnswerBody) -> dict[str, Any]:
    system_prompt = (
        "Ты аналитик цифровой личности. Отвечай только валидным JSON без markdown."
    )
    user_prompt = f"""
Вопрос: {body.question}
Ответ Мустафы: {body.answer}

Извлеки одно убеждение или правило стиля.
Верни СТРОГО JSON:
{{
  "topic": "тема",
  "rules_and_values": "сформулированное правило",
  "candidate_type": "belief"
}}
"""
    parsed = await _generate_json(system_prompt, user_prompt)
    candidate_type = parsed.get("candidate_type") or "belief"
    topic = parsed.get("topic") or "Убеждение"
    rules_and_values = parsed.get("rules_and_values") or body.answer
    result = {
        "topic": topic,
        "rules_and_values": rules_and_values,
        "candidate_type": candidate_type,
        "candidate_id": None,
    }
    if body.create_candidate:
        row = await asyncio.to_thread(
            create_memory_candidate,
            candidate_type=candidate_type,
            topic=topic,
            content={
                "topic": topic,
                "rules_and_values": rules_and_values,
                "source_quote": body.answer,
            },
            source_type="note",
            source_id="studio_interview",
            source_quote=body.answer,
            confidence=0.85,
            metadata={"question": body.question, "origin": "studio_interview"},
        )
        if row:
            result["candidate_id"] = row.get("id")
    return result
