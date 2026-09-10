"""Inline keyboards for the V2.1 editor and review workflow."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def intent_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Улучшить текст", callback_data=f"intent:improve:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Развить идею", callback_data=f"intent:develop:{draft_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Запомнить обо мне", callback_data=f"intent:memory:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Правило моего стиля",
                    callback_data=f"intent:style_rule:{draft_id}",
                ),
            ],
        ]
    )


def length_keyboard(draft_id: str, recommended: str) -> InlineKeyboardMarkup:
    labels = {
        "short": "Коротко",
        "normal": "Обычно",
        "expanded": "Раскрыть",
    }
    buttons = []
    for slug in ("short", "normal", "expanded"):
        suffix = " ✓" if slug == recommended else ""
        buttons.append(
            InlineKeyboardButton(
                text=labels[slug] + suffix,
                callback_data=f"length:{slug}:{draft_id}",
            )
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def style_mode_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Мой голос", callback_data=f"style:my_voice:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Кратко и ясно", callback_data=f"style:clear:{draft_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Storytelling", callback_data=f"style:storytelling:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Покажи процесс",
                    callback_data=f"style:show_your_work:{draft_id}",
                ),
            ],
        ]
    )


def youtube_presentation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="От моего имени",
                    callback_data="ytmode:personal",
                ),
                InlineKeyboardButton(
                    text="Через видео + ссылка",
                    callback_data="ytmode:source_reference",
                ),
            ]
        ]
    )


def draft_actions_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Исправить", callback_data=f"draft:correct:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Альтернатива", callback_data=f"draft:alternative:{draft_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Короче", callback_data=f"draft:shorter:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Подробнее", callback_data=f"draft:longer:{draft_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Сменить режим", callback_data=f"draft:style:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Финальный preview", callback_data=f"draft:preview:{draft_id}"
                ),
            ],
        ]
    )


def correction_scope_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Только этот текст", callback_data=f"scope:local:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Запомнить правило", callback_data=f"scope:remember:{draft_id}"
                ),
            ]
        ]
    )


def publish_confirm_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Опубликовать", callback_data=f"publish:confirm:{draft_id}"
                ),
                InlineKeyboardButton(
                    text="Вернуться", callback_data=f"publish:back:{draft_id}"
                ),
            ]
        ]
    )


def review_keyboard(kind: str, record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Принять", callback_data=f"review:accept:{kind}:{record_id}"
                ),
                InlineKeyboardButton(
                    text="Изменить", callback_data=f"review:edit:{kind}:{record_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отклонить", callback_data=f"review:reject:{kind}:{record_id}"
                ),
                InlineKeyboardButton(
                    text="Пропустить", callback_data=f"review:skip:{kind}:{record_id}"
                ),
            ],
        ]
    )


# Compatibility aliases retained for imports in older tests/tools.
def brainstorm_keyboard(session_id: str | None = None) -> InlineKeyboardMarkup:
    return draft_actions_keyboard(session_id or "legacy")


def publish_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return draft_actions_keyboard(session_id)


def note_confirm_keyboard(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сохранить", callback_data=f"note:save:{session_id}"
                ),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"note:cancel:{session_id}"
                ),
            ]
        ]
    )
