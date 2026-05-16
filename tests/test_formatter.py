from db import Reminder
from formatter import (
    format_reminders_list,
    format_reminder_advance,
    format_reminder_fire,
    fmt_fire_at,
)


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


def test_fmt_fire_at_relative():
    import time
    future = int(time.time()) + 30 * 60
    out = fmt_fire_at(future, "Europe/Moscow")
    assert "через" in out
    past = int(time.time()) - 60
    assert "прошло" in fmt_fire_at(past, "Europe/Moscow")
