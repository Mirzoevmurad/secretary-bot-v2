from db import Note, Reminder
from formatter import (
    format_note,
    format_list,
    format_search,
    format_reminders_list,
    format_reminder_advance,
    format_reminder_fire,
    fmt_fire_at,
)


def _note(note_id: int = 1, **overrides) -> Note:
    summary = {
        "title": "Тест заметки",
        "details": "Развёрнутый пересказ заметки в один абзац: первый пункт и второй пункт.",
        "tasks": [{"what": "сделать отчёт", "who": "я", "when": "завтра"}],
        "open_questions": ["когда релиз?"],
        "tags": ["работа", "отчёт"],
        "category": "Работа",
    }
    summary.update(overrides.pop("summary_overrides", {}))
    return Note(
        note_id=note_id,
        user_id=1,
        created_at=1700000000,
        duration_seconds=75.0,
        lang="ru",
        title=summary["title"],
        transcript="транскрипт тест",
        summary=summary,
        audio_path=None,
        source="voice",
    )


def test_format_note_has_all_sections():
    out = format_note(_note())
    assert "Заметка #1" in out
    assert "Тест заметки" in out
    assert "Развёрнутый пересказ" in out
    assert "Задачи" in out and "сделать отчёт" in out and "👤 я" in out and "📅 завтра" in out
    assert "Открытые вопросы" in out and "когда релиз?" in out
    assert "#работа" in out and "#отчёт" in out
    assert "/get_1" in out


def test_format_note_no_duplicated_summary_blocks():
    """Главное / TL;DR убраны, чтобы не повторять details разными форматами."""
    out = format_note(_note())
    assert "Главное" not in out
    assert "TL;DR" not in out


def test_format_note_escapes_html():
    n = _note(summary_overrides={"title": "<script>", "details": "<b>x</b>", "tasks": [], "open_questions": [], "tags": []})
    out = format_note(n)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out or "&lt;" in out


def test_format_list_empty():
    assert "пока нет" in format_list([])


def test_format_list_sorted_display():
    n = _note()
    out = format_list([n])
    assert "#1" in out and "Тест заметки" in out


def test_format_search_no_results():
    out = format_search([], "тест")
    assert "Ничего не нашёл" in out
    assert "тест" in out


def test_format_search_with_results():
    out = format_search([_note(42)], "отчёт")
    assert "#42" in out
    assert "отчёт" in out


def _reminder(rid: int = 5, fire_at: int = 9999999999, advance: int = 5) -> Reminder:
    return Reminder(
        reminder_id=rid,
        user_id=1,
        created_at=1700000000,
        fire_at=fire_at,
        advance_minutes=advance,
        text="Встреча с тимлидом",
        source_note_id=None,
        status="pending",
        fired_at=None,
    )


def test_format_reminders_list_empty():
    out = format_reminders_list([])
    assert "Напоминаний нет" in out


def test_format_reminders_list_renders():
    out = format_reminders_list([_reminder()])
    assert "#5" in out
    assert "Встреча с тимлидом" in out
    assert "Отменить: /cancel_5" in out


def test_format_reminder_advance():
    out = format_reminder_advance(_reminder())
    assert "Скоро напоминание" in out
    assert "Встреча с тимлидом" in out


def test_format_reminder_fire():
    out = format_reminder_fire(_reminder())
    assert "Сейчас" in out
    assert "Встреча с тимлидом" in out


def test_format_note_with_reminders_section():
    out = format_note(_note(), scheduled_reminders=[_reminder()])
    assert "Напоминания" in out
    assert "Встреча с тимлидом" in out


def test_format_note_text_source_emoji():
    n = _note()
    n.source = "text"
    n.duration_seconds = None
    out = format_note(n)
    assert "📝 <b>Заметка #1</b>" in out
    # ⏱ не должен быть когда нет длительности
    assert "⏱" not in out


def test_fmt_fire_at_relative():
    import time
    future = int(time.time()) + 30 * 60
    out = fmt_fire_at(future, "Europe/Moscow")
    assert "через" in out
    past = int(time.time()) - 60
    assert "прошло" in fmt_fire_at(past, "Europe/Moscow")
