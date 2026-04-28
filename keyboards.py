"""InlineKeyboardMarkup builders для заметок и напоминаний.

Callback-data формат: короткие префиксы (Telegram limit = 64 байта).
- n:e:title:42       — edit note title
- n:e:cat:42         — edit note category
- n:e:tags:42        — edit note tags
- n:del:42           — ask to delete note (показать confirm)
- n:open:42          — open note detail (из списка)
- n:del_yes:42       — confirm delete
- n:polish:42        — полировать транскрипт LLM
- r:e:time:5         — edit reminder fire_at
- r:e:text:5         — edit reminder text
- r:cancel:5         — ask to cancel reminder (confirm)
- r:cancel_yes:5     — confirm cancel
- nop                — кнопка-разделитель / отмена пришедшего prompt
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def note_actions_kb(note_id: int) -> InlineKeyboardMarkup:
    """Кнопки под одиночной заметкой."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Заголовок", callback_data=f"n:e:title:{note_id}"),
                InlineKeyboardButton("🏷 Категория", callback_data=f"n:e:cat:{note_id}"),
            ],
            [
                InlineKeyboardButton("# Теги", callback_data=f"n:e:tags:{note_id}"),
                InlineKeyboardButton("📝 Полировать", callback_data=f"n:polish:{note_id}"),
            ],
            [
                InlineKeyboardButton("🗑 Удалить", callback_data=f"n:del:{note_id}"),
            ],
        ]
    )


def note_list_kb(note_ids: list[int]) -> InlineKeyboardMarkup:
    """Кнопка «Открыть #N» для каждой заметки в /list. Не более 25 заметок (TG лимит ~100 кнопок)."""
    rows = [
        [InlineKeyboardButton(f"📄 Открыть #{nid}", callback_data=f"n:open:{nid}")]
        for nid in note_ids[:25]
    ]
    return InlineKeyboardMarkup(rows) if rows else InlineKeyboardMarkup([])


def confirm_delete_note_kb(note_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"n:del_yes:{note_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data="nop"),
            ]
        ]
    )


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
