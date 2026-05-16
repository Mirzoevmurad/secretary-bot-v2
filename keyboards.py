"""InlineKeyboardMarkup builders для напоминаний и полировки/перевода.

Callback-data формат: короткие префиксы (Telegram limit = 64 байта).
- r:e:time:5         — edit reminder fire_at
- r:e:text:5         — edit reminder text
- r:cancel:5         — ask to cancel reminder (confirm)
- r:cancel_yes:5     — confirm cancel
- x:tr:<token>       — translate cached polished text
- x:edit:<token>     — edit cached polished text and translate
- x:back:<token>     — restore original (после перевода)
- x:cancel_edit      — отменить активный pending_edit (без ForceReply)
- nop                — кнопка-разделитель / отмена пришедшего prompt
"""
from __future__ import annotations

from telegram import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup


def reminder_actions_kb(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⏰ Перенести", callback_data=f"r:e:time:{reminder_id}"),
                InlineKeyboardButton("✏️ Текст", callback_data=f"r:e:text:{reminder_id}"),
            ],
            [
                InlineKeyboardButton("🗑 Отменить", callback_data=f"r:cancel:{reminder_id}"),
            ],
        ]
    )


def reminders_list_kb(reminder_ids: list[int]) -> InlineKeyboardMarkup:
    """По строке на каждое напоминание: ⏰ перенести · ✏️ текст · 🗑 отменить."""
    rows = []
    for rid in reminder_ids[:20]:
        rows.append(
            [
                InlineKeyboardButton(f"⏰ #{rid}", callback_data=f"r:e:time:{rid}"),
                InlineKeyboardButton(f"✏️ #{rid}", callback_data=f"r:e:text:{rid}"),
                InlineKeyboardButton(f"🗑 #{rid}", callback_data=f"r:cancel:{rid}"),
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def confirm_cancel_reminder_kb(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да, отменить", callback_data=f"r:cancel_yes:{reminder_id}"),
                InlineKeyboardButton("❌ Назад", callback_data="nop"),
            ]
        ]
    )


# ---- Полировка / перевод -------------------------------------------------

# Telegram CopyTextButton ограничивает длину поля text 256 символами. Если
# полированный текст длиннее — кнопку «Копировать» либо не показываем, либо
# вешаем на сокращённый фрагмент. Мы выбираем простой путь: при длине
# больше лимита — кнопка «Копировать» не строится; пользователь копирует
# выделением вручную.
COPY_BUTTON_LIMIT = 256


def cancel_edit_kb(current_value: str | None = None) -> InlineKeyboardMarkup:
    """Под промптом «Пришлите новое значение».
    Если передан current_value и он умещается в CopyTextButton (≤256 символов
    и непустой) — добавляем кнопку «📋 Скопировать текущее» отдельной строкой,
    чтобы пользователь по тапу скопировал текущее значение, вставил в поле
    ввода и поправил, не набирая всё с нуля.
    Внизу — кнопка «❌ Отмена» (callback x:cancel_edit).
    """
    rows: list[list[InlineKeyboardButton]] = []
    copy_btn = _copy_button(current_value or "", "📋 Скопировать текущее")
    if copy_btn is not None:
        rows.append([copy_btn])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="x:cancel_edit")])
    return InlineKeyboardMarkup(rows)


def _copy_button(text: str, label: str) -> InlineKeyboardButton | None:
    if not text or len(text) > COPY_BUTTON_LIMIT:
        return None
    return InlineKeyboardButton(label, copy_text=CopyTextButton(text=text))


def polish_actions_kb(
    token: str,
    polished_text: str,
) -> InlineKeyboardMarkup:
    """Кнопки под полированным ответом.
    Содержит:
      - 📋 Скопировать (CopyTextButton с самим текстом, по тапу — clipboard);
      - 🌍 Перевести (RU↔EN, направление автоматическое).
    """
    rows: list[list[InlineKeyboardButton]] = []
    copy_btn = _copy_button(polished_text, "📋 Скопировать")
    if copy_btn is not None:
        rows.append([copy_btn])
    action_row = [
        InlineKeyboardButton("🌍 Перевести", callback_data=f"x:tr:{token}"),
    ]
    rows.append(action_row)
    return InlineKeyboardMarkup(rows)


def translate_actions_kb(
    token: str, source_text: str, translated_text: str,
) -> InlineKeyboardMarkup:
    """Под текстом, который бот вывел как «оригинал» в режиме перевода.
    Содержит:
      - 📋 Скопировать оригинал
      - 🌍 Перевести
      - ✏️ Исправить и перевести (опционально, через состояние)
    """
    rows: list[list[InlineKeyboardButton]] = []
    copy_btn = _copy_button(source_text, "📋 Скопировать оригинал")
    if copy_btn is not None:
        rows.append([copy_btn])
    if translated_text:
        # уже переведено — показываем кнопку «копировать перевод» и «вернуть оригинал»
        copy_tr = _copy_button(translated_text, "📋 Скопировать перевод")
        if copy_tr is not None:
            rows.append([copy_tr])
    rows.append([
        InlineKeyboardButton("🌍 Перевести", callback_data=f"x:tr:{token}"),
        InlineKeyboardButton("✏️ Исправить", callback_data=f"x:edit:{token}"),
    ])
    return InlineKeyboardMarkup(rows)


def translated_view_kb(
    token: str, source_text: str, translated_text: str,
) -> InlineKeyboardMarkup:
    """Под выведённым переводом."""
    rows: list[list[InlineKeyboardButton]] = []
    copy_btn = _copy_button(translated_text, "📋 Скопировать перевод")
    if copy_btn is not None:
        rows.append([copy_btn])
    copy_src = _copy_button(source_text, "📋 Скопировать оригинал")
    if copy_src is not None:
        rows.append([copy_src])
    rows.append([
        InlineKeyboardButton("🔁 Показать оригинал", callback_data=f"x:back:{token}"),
        InlineKeyboardButton("✏️ Исправить", callback_data=f"x:edit:{token}"),
    ])
    return InlineKeyboardMarkup(rows)
