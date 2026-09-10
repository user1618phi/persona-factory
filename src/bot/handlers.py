"""Private Telegram editor, review queue and publishing workflow."""

from __future__ import annotations

import asyncio
import html
import io
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from src.bot.auth import is_owner as _is_owner
from src.bot.channel_sync import router as channel_sync_router
from src.bot.keyboards import (
    correction_scope_keyboard,
    draft_actions_keyboard,
    intent_keyboard,
    length_keyboard,
    publish_confirm_keyboard,
    review_keyboard,
    style_mode_keyboard,
    youtube_presentation_keyboard,
)
from src.bot.ops_state import record_youtube_error, record_youtube_success
from src.bot.session_store import load_session, save_session
from src.bot.status_report import format_production_status
from src.config import get_settings
from src.db.memory_repository import (
    accept_review_item,
    add_draft_version,
    create_draft,
    edit_review_item,
    get_draft,
    latest_draft_for_owner,
    next_review_item,
    reject_review_item,
    skip_review_item,
    update_draft,
)
from src.db.supabase_client import get_supabase
from src.orchestrator.brainstorm import (
    generate_draft,
    recommend_length,
    rewrite_draft,
)
from src.orchestrator.learning import (
    create_candidates_from_text,
    create_style_correction_candidate,
)
from src.orchestrator.llm_router import LLMRouterError, LLM_UNAVAILABLE_MESSAGE
from src.orchestrator.publisher import PublishError, publish_draft
from src.orchestrator.transcriber import transcribe_audio
from src.orchestrator.youtube_ingest import (
    YouTubeIngestError,
    build_youtube_editor_result,
    extract_youtube_urls,
    strip_youtube_urls,
)

logger = logging.getLogger(__name__)
router = Router()
YOUTUBE_CANCEL_TEXTS = {"/cancel", "cancel", "отмена", "стоп"}


async def _to_thread(func, /, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def safe_callback_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    """Acknowledge Telegram callbacks without crashing on stale query ids."""
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("Telegram callback answer failed: %s", exc)


def _review_text(item: dict[str, Any]) -> str:
    kind = item["kind"]
    row = item["record"]
    if kind == "rule":
        body = (
            f"Тип: {row.get('rule_type')}\n"
            f"Тема: {row.get('topic')}\n\n{row.get('content')}"
        )
    elif kind == "experience":
        body = (
            f"{'Сожаление' if row.get('is_regret') else 'Опыт'}: "
            f"{row.get('event_title')}\n\n"
            f"Контекст: {row.get('context')}\n"
            f"Выбор: {row.get('choice_made')}\n"
            f"Урок: {row.get('consequences_lessons')}"
        )
    else:
        body = (
            f"Кандидат: {row.get('candidate_type')}\n"
            f"Тема: {row.get('topic') or '—'}\n\n"
            f"{row.get('content')}"
        )
    return html.escape(body)


async def _show_next_review(message: Message) -> None:
    item = await _to_thread(next_review_item)
    if not item:
        await message.answer("Очередь review пуста.")
        return
    row = item["record"]
    await message.answer(
        _review_text(item),
        reply_markup=review_keyboard(item["kind"], int(row["id"])),
    )


async def _start_editor(
    message: Message,
    text: str,
    *,
    owner_user_id: int | None = None,
) -> None:
    user_id = owner_user_id or (message.from_user.id if message.from_user else 0)
    journal = (
        get_supabase()
        .table("journal_entries")
        .insert(
            {
                "raw_text": text,
                "source_type": "note",
                "applied": False,
                "intent": None,
                "processing_state": "received",
            }
        )
        .execute()
    )
    journal_id = (journal.data or [{}])[0].get("id")
    draft = await _to_thread(
        create_draft,
        owner_user_id=user_id,
        raw_input=text,
        intent="develop",
        length_mode=recommend_length(text, intent="develop"),
        journal_id=journal_id,
    )
    if journal_id:
        get_supabase().table("journal_entries").update({"draft_id": draft["id"]}).eq(
            "id", journal_id
        ).execute()
    await message.answer(
        "Что сделать с этим текстом?",
        reply_markup=intent_keyboard(str(draft["id"])),
    )


async def _start_youtube_editor(
    message: Message,
    *,
    url: str,
    user_takeaway: str,
) -> bool:
    status = await message.answer(
        "Разбираю YouTube: беру субтитры, если они есть; если нет — расшифрую аудио. "
        "Потом уберу рекламу и воду."
    )
    try:
        result = await build_youtube_editor_result(
            url,
            user_takeaway=user_takeaway,
        )
        record_youtube_success(
            url=url,
            source=result.transcript_source,
            duration=result.duration,
            processed_chunks=result.processed_chunks,
            total_chunks=result.total_chunks,
            coverage=result.coverage,
        )
    except YouTubeIngestError as exc:
        record_youtube_error(str(exc))
        await status.edit_text(f"YouTube не обработался: {html.escape(str(exc))}")
        return False
    except Exception as exc:  # noqa: BLE001
        logger.exception("YouTube ingest failed")
        record_youtube_error(str(exc))
        await status.edit_text(f"Ошибка YouTube ingest: {html.escape(str(exc))}")
        return False
    user_id = message.from_user.id if message.from_user else 0
    session = load_session(user_id)
    session["pending_youtube_editor"] = {
        "url": url,
        "title": result.title,
        "mode_inputs": result.mode_inputs,
    }
    save_session(user_id, session)
    await status.edit_text("YouTube разобран. Выберите, как подать материал.")
    await message.answer(
        "Как написать будущий текст?",
        reply_markup=youtube_presentation_keyboard(),
    )
    return True


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    await message.answer(
        "The Persona Factory V2.1 — редактор-двойник.\n\n"
        "Отправьте обычный текст, мысль или черновик.\n"
        "Можно отправить YouTube-ссылку с вашим комментарием или сначала только ссылку.\n"
        "/review — проверить новые сведения о вас\n"
        "/sync status — состояние синхронизации канала\n"
        "/status — состояние production\n"
        "/help — список всех функций"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return

    help_text = (
        "<b>Доступные функции и команды:</b>\n\n"
        "📝 <b>Создание и редактирование постов:</b>\n"
        "Просто отправьте любой текст (мысль, черновик) сообщением, либо используйте команды:\n"
        "• <code>/note [текст]</code>\n"
        "• <code>/brainstorm [текст]</code>\n"
        "Бот предложит выбрать цель (Развить, Улучшить, Запомнить факт) и запустит процесс редактуры.\n\n"
        "▶️ <b>YouTube:</b>\n"
        "Отправьте ссылку YouTube вместе с текстом о том, что понравилось или было полезно. "
        "Если отправить только ссылку, бот дождётся следующего текста или голосового комментария. "
        "Видео разбирается через субтитры, а если их нет — через аудио-расшифровку. "
        "После разбора можно выбрать подачу: от вашего имени без упоминания видео "
        "или с естественным упоминанием видео и ссылкой в конце.\n\n"
        "🧠 <b>База знаний и память:</b>\n"
        "• <code>/review</code> — проверить очередь новых сведений о вас (правила стиля, новые факты из текстов). Без вашего подтверждения новые правила не применяются.\n\n"
        "⚙️ <b>Система и статус:</b>\n"
        "• <code>/status</code> — текущее состояние системы, доступность LLM и количество записей в памяти.\n"
        "• <code>/sync status</code> — состояние синхронизации с каналом Telegram.\n"
        "• <code>/help</code> — это сообщение.\n\n"
        "<b>Как работает процесс:</b>\n"
        "1. Отправьте текст.\n"
        "2. Выберите цель, длину и стиль (например, Storytelling или Clear).\n"
        "3. Бот напишет текст вашим голосом.\n"
        "4. Вы можете попросить <i>Сделать короче</i>, <i>Подробнее</i>, или отправить <b>свою правку текстом</b>.\n"
        "5. Если правка системная — её можно сохранить как постоянное правило (уйдет в /review).\n"
        "6. Когда всё готово — нажмите «Preview» и «Опубликовать»."
    )

    await message.answer(help_text)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    await message.answer(await _to_thread(format_production_status))


@router.message(Command("review"))
async def cmd_review(message: Message) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    await _show_next_review(message)


@router.message(Command("brainstorm"))
@router.message(Command("note"))
async def cmd_legacy_editor(message: Message, command: CommandObject) -> None:
    if not _is_owner(message.from_user.id if message.from_user else None):
        return
    text = (command.args or "").strip()
    if not text:
        await message.answer(
            "Добавьте текст после команды или просто отправьте его сообщением."
        )
        return
    await _start_editor(message, text)


@router.callback_query(F.data.startswith("intent:"))
async def on_intent(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, intent, draft_id = (callback.data or "").split(":", 2)
    await safe_callback_answer(callback)
    draft = await _to_thread(get_draft, draft_id)
    if not draft:
        await callback.message.answer("Черновик не найден.")
        return
    if intent in {"memory", "style_rule"}:
        if intent == "memory":
            created = await create_candidates_from_text(
                draft["raw_input"],
                source_type="note",
                source_id=draft_id,
                allow_facts=True,
            )
        else:
            candidate = await _to_thread(
                create_style_correction_candidate,
                draft["raw_input"],
                draft_id=draft_id,
            )
            created = [candidate] if candidate else []
        await _to_thread(update_draft, draft_id, intent=intent, state="cancelled")
        if draft.get("journal_id"):
            get_supabase().table("journal_entries").update(
                {
                    "intent": intent,
                    "processing_state": "review_pending",
                    "ingest_report": {"memory_candidates": len(created)},
                }
            ).eq("id", draft["journal_id"]).execute()
        await callback.message.answer(
            f"Создано кандидатов для review: {len(created)}. Используйте /review."
        )
        return

    recommended = recommend_length(draft["raw_input"], intent=intent)
    await _to_thread(
        update_draft,
        draft_id,
        intent=intent,
        length_mode=recommended,
        state="configuring",
    )
    await callback.message.answer(
        f"Рекомендуемый объём: {recommended}. Выберите длину:",
        reply_markup=length_keyboard(draft_id, recommended),
    )


@router.callback_query(F.data.startswith("ytmode:"))
async def on_youtube_presentation_mode(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, mode = (callback.data or "").split(":", 1)
    await safe_callback_answer(callback)
    session = load_session(callback.from_user.id)
    pending = session.get("pending_youtube_editor") or {}
    mode_inputs = pending.get("mode_inputs") or {}
    editor_input = mode_inputs.get(mode)
    if not editor_input:
        await callback.message.answer(
            "YouTube-материал не найден. Отправьте ссылку ещё раз."
        )
        return
    session.pop("pending_youtube_editor", None)
    save_session(callback.from_user.id, session)
    label = (
        "от моего имени"
        if mode == "personal"
        else "с упоминанием видео и ссылкой в конце"
    )
    await callback.message.answer(f"Выбрано: {label}.")
    await _start_editor(
        callback.message,
        editor_input,
        owner_user_id=callback.from_user.id,
    )


@router.callback_query(F.data.startswith("length:"))
async def on_length(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, length_mode, draft_id = (callback.data or "").split(":", 2)
    await safe_callback_answer(callback)
    await _to_thread(update_draft, draft_id, length_mode=length_mode)
    await callback.message.answer(
        "Выберите технику подачи:",
        reply_markup=style_mode_keyboard(draft_id),
    )


async def _generate_and_send(
    callback: CallbackQuery, draft_id: str, style_mode: str
) -> None:
    await safe_callback_answer(callback)
    draft = await _to_thread(get_draft, draft_id)
    if not draft:
        await callback.message.answer("Черновик не найден.")
        return
    await _to_thread(update_draft, draft_id, style_mode=style_mode, state="generating")
    await callback.message.answer("Готовлю один основной черновик…")
    try:
        text, meta = await generate_draft(
            raw_input=draft["raw_input"],
            intent=draft["intent"],
            length_mode=draft["length_mode"],
            style_mode=style_mode,
            draft_id=draft_id,
        )
        await _to_thread(
            add_draft_version,
            draft_id,
            text=text,
            change_type="generated",
        )
        if draft.get("journal_id"):
            get_supabase().table("journal_entries").update(
                {
                    "intent": draft["intent"],
                    "processing_state": "drafted",
                }
            ).eq("id", draft["journal_id"]).execute()
        await callback.message.answer(
            html.escape(text),
            reply_markup=draft_actions_keyboard(draft_id),
        )
        logger.info("draft=%s generated meta=%s", draft_id, meta)
    except LLMRouterError:
        await _to_thread(update_draft, draft_id, state="failed")
        await callback.message.answer(LLM_UNAVAILABLE_MESSAGE)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Draft generation failed")
        await _to_thread(update_draft, draft_id, state="failed")
        await callback.message.answer(f"Ошибка генерации: {html.escape(str(exc))}")


@router.callback_query(F.data.startswith("style:"))
async def on_style(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, style_mode, draft_id = (callback.data or "").split(":", 2)
    await _generate_and_send(callback, draft_id, style_mode)


@router.callback_query(F.data.startswith("draft:"))
async def on_draft_action(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, action, draft_id = (callback.data or "").split(":", 2)
    await safe_callback_answer(callback)
    draft = await _to_thread(get_draft, draft_id)
    if not draft:
        await callback.message.answer("Черновик не найден.")
        return
    current = (draft.get("final_text") or "").strip()

    if action == "correct":
        await _to_thread(update_draft, draft_id, state="correcting")
        await callback.message.answer("Напишите конкретную корректировку.")
        return
    if action == "style":
        await callback.message.answer(
            "Выберите новый режим:", reply_markup=style_mode_keyboard(draft_id)
        )
        return
    if action == "preview":
        await _to_thread(update_draft, draft_id, state="preview")
        await callback.message.answer(
            "Финальная версия:\n\n" + html.escape(current),
            reply_markup=publish_confirm_keyboard(draft_id),
        )
        return

    instructions = {
        "alternative": "Сделай альтернативную структуру без новых фактов.",
        "shorter": "Сделай заметно короче. Оставь одну главную мысль.",
        "longer": "Раскрой подробнее только на основе доступных фактов и опыта.",
    }
    instruction = instructions.get(action)
    if not instruction:
        return
    target_length = (
        "short"
        if action == "shorter"
        else "expanded"
        if action == "longer"
        else draft["length_mode"]
    )
    try:
        rewritten = await rewrite_draft(
            raw_input=draft["raw_input"],
            current_text=current,
            instruction=instruction,
            length_mode=target_length,
            style_mode=draft["style_mode"],
        )
        await _to_thread(
            update_draft, draft_id, length_mode=target_length, state="drafted"
        )
        await _to_thread(
            add_draft_version,
            draft_id,
            text=rewritten,
            change_type=action,
            instruction=instruction,
        )
        await callback.message.answer(
            html.escape(rewritten), reply_markup=draft_actions_keyboard(draft_id)
        )
    except Exception as exc:  # noqa: BLE001
        await callback.message.answer(f"Ошибка: {html.escape(str(exc))}")


@router.callback_query(F.data.startswith("scope:"))
async def on_correction_scope(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, scope, draft_id = (callback.data or "").split(":", 2)
    await safe_callback_answer(callback)
    draft = await _to_thread(get_draft, draft_id)
    if not draft or not draft.get("pending_instruction"):
        await callback.message.answer("Корректировка не найдена.")
        return
    instruction = draft["pending_instruction"]
    if scope == "remember":
        await _to_thread(
            create_style_correction_candidate,
            instruction,
            draft_id=draft_id,
        )
    rewritten = await rewrite_draft(
        raw_input=draft["raw_input"],
        current_text=draft.get("final_text") or "",
        instruction=instruction,
        length_mode=draft["length_mode"],
        style_mode=draft["style_mode"],
    )
    await _to_thread(
        add_draft_version,
        draft_id,
        text=rewritten,
        change_type="rewrite",
        instruction=instruction,
    )
    suffix = "\n\nПравило добавлено в /review." if scope == "remember" else ""
    await callback.message.answer(
        html.escape(rewritten) + suffix,
        reply_markup=draft_actions_keyboard(draft_id),
    )


@router.callback_query(F.data.startswith("publish:"))
async def on_publish(callback: CallbackQuery, bot: Bot) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, action, draft_id = (callback.data or "").split(":", 2)
    await safe_callback_answer(callback)
    if action == "back":
        await _to_thread(update_draft, draft_id, state="drafted")
        draft = await _to_thread(get_draft, draft_id)
        await callback.message.answer(
            html.escape((draft or {}).get("final_text") or ""),
            reply_markup=draft_actions_keyboard(draft_id),
        )
        return
    try:
        message_id, created = await publish_draft(bot, draft_id)
        await callback.message.answer(
            (
                f"Опубликовано, message_id={message_id}."
                if created
                else f"Этот черновик уже опубликован, message_id={message_id}."
            )
        )
    except PublishError as exc:
        await callback.message.answer(html.escape(str(exc)))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Publishing failed")
        await callback.message.answer(f"Ошибка публикации: {html.escape(str(exc))}")


@router.callback_query(F.data.startswith("review:"))
async def on_review(callback: CallbackQuery) -> None:
    if not _is_owner(callback.from_user.id):
        return
    _, action, kind, raw_id = (callback.data or "").split(":", 3)
    await safe_callback_answer(callback)
    record_id = int(raw_id)
    if action == "accept":
        await _to_thread(accept_review_item, kind, record_id, callback.from_user.id)
    elif action == "reject":
        await _to_thread(reject_review_item, kind, record_id, callback.from_user.id)
    elif action == "skip":
        await _to_thread(skip_review_item, kind, record_id)
    elif action == "edit":
        session = load_session(callback.from_user.id)
        session["review_edit"] = {"kind": kind, "record_id": record_id}
        save_session(callback.from_user.id, session)
        await callback.message.answer(
            "Пришлите исправленную формулировку. Для истории она заменит поле «урок»."
        )
        return
    await callback.message.answer("Сохранено.")
    await _show_next_review(callback.message)


@router.message(F.voice)
async def on_voice(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not _is_owner(user_id) or not message.voice:
        return

    wait_msg = await message.answer("🎙 Слушаю и расшифровываю...")
    try:
        file = await bot.get_file(message.voice.file_id)
        file_obj = io.BytesIO()
        await bot.download_file(file.file_path, destination=file_obj)

        text = await transcribe_audio(file_obj.getvalue())

        if not text:
            await wait_msg.edit_text("Не удалось распознать голос (пустой текст).")
            return

        await wait_msg.delete()
        await message.answer(f"<i>Распознано:</i>\n{html.escape(text)}")
        session = load_session(user_id or 0)
        pending_youtube = session.get("pending_youtube")
        if pending_youtube:
            created = await _start_youtube_editor(
                message,
                url=str(pending_youtube["url"]),
                user_takeaway=text,
            )
            if created:
                session.pop("pending_youtube", None)
                save_session(user_id or 0, session)
            return
        await _start_editor(message, text)

    except Exception as exc:
        logger.exception("Voice transcription failed")
        await wait_msg.edit_text(f"Ошибка распознавания: {html.escape(str(exc))}")


@router.message(F.text)
async def on_text(message: Message) -> None:
    user_id = message.from_user.id if message.from_user else None
    if not _is_owner(user_id) or not message.text:
        return
    session = load_session(user_id or 0)
    review_edit = session.get("review_edit")
    if review_edit:
        await _to_thread(
            edit_review_item,
            review_edit["kind"],
            int(review_edit["record_id"]),
            message.text,
        )
        session.pop("review_edit", None)
        save_session(user_id or 0, session)
        await message.answer("Формулировка обновлена.")
        await _show_next_review(message)
        return

    pending_youtube_editor = session.get("pending_youtube_editor")
    if pending_youtube_editor and message.text.strip().lower() in YOUTUBE_CANCEL_TEXTS:
        session.pop("pending_youtube_editor", None)
        save_session(user_id or 0, session)
        await message.answer("Выбор подачи YouTube отменён.")
        return

    draft = await _to_thread(
        latest_draft_for_owner,
        user_id or 0,
        states=("correcting",),
    )
    if draft:
        await _to_thread(
            update_draft,
            draft["id"],
            pending_instruction=message.text,
            state="drafted",
        )
        await message.answer(
            "Эта корректировка только для текущего текста или её нужно запомнить?",
            reply_markup=correction_scope_keyboard(str(draft["id"])),
        )
        return

    pending_youtube = session.get("pending_youtube")
    urls = extract_youtube_urls(message.text)
    if pending_youtube:
        normalized = message.text.strip().lower()
        if normalized in YOUTUBE_CANCEL_TEXTS:
            session.pop("pending_youtube", None)
            save_session(user_id or 0, session)
            await message.answer("Ожидание комментария к YouTube отменено.")
            return
        url = urls[0] if urls else str(pending_youtube["url"])
        user_takeaway = strip_youtube_urls(message.text) if urls else message.text
        created = await _start_youtube_editor(
            message,
            url=url,
            user_takeaway=user_takeaway,
        )
        if created:
            session.pop("pending_youtube", None)
            save_session(user_id or 0, session)
        return

    if urls:
        url = urls[0]
        user_takeaway = strip_youtube_urls(message.text)
        if not user_takeaway:
            session["pending_youtube"] = {"url": url}
            save_session(user_id or 0, session)
            await message.answer(
                "Ссылку получил. Пришлите текстом или голосом, что именно вам "
                "понравилось, было полезно или стало новым. "
                "Если передумали — /cancel."
            )
            return
        await _start_youtube_editor(
            message,
            url=url,
            user_takeaway=user_takeaway,
        )
        return

    await _start_editor(message, message.text)


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(channel_sync_router)
    return dp


async def run_bot(bot: Bot) -> None:
    await create_dispatcher().start_polling(bot)
