"""Tests for reminders: parsing, DB CRUD, materialize, iCal export."""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from db import Database
from llm import ReminderSpec
from reminders import (
    materialize_reminders,
    now_context_block,
    parse_iso_to_epoch,
    reminders_to_ical,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.sqlite")


def test_parse_iso_with_offset() -> None:
    epoch = parse_iso_to_epoch("2026-04-26T12:30:00+03:00", "Europe/Moscow")
    expected = datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc).timestamp()
    assert epoch == int(expected)


def test_parse_iso_z_suffix() -> None:
    epoch = parse_iso_to_epoch("2026-04-26T09:30:00Z", "Europe/Moscow")
    assert epoch == int(datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc).timestamp())


def test_parse_iso_naive_uses_default_tz() -> None:
    epoch = parse_iso_to_epoch("2026-04-26T12:30:00", "Europe/Moscow")
    # Москва +03 → 09:30 UTC
    assert epoch == int(datetime(2026, 4, 26, 9, 30, tzinfo=timezone.utc).timestamp())


def test_parse_iso_invalid() -> None:
    assert parse_iso_to_epoch("это не дата", "Europe/Moscow") is None
    assert parse_iso_to_epoch("", "Europe/Moscow") is None


def test_now_context_includes_tz() -> None:
    block = now_context_block("Europe/Moscow")
    assert "Europe/Moscow" in block
    assert "fire_at_iso" in block


def test_db_add_and_list_reminder(db: Database) -> None:
    db.upsert_user(42, "u", "User", None)
    fire_at = int(time.time()) + 3600
    rid = db.add_reminder(
        user_id=42,
        fire_at=fire_at,
        advance_minutes=5,
        text="Встреча с тимлидом",
        source_note_id=None,
    )
    assert rid > 0
    r = db.get_reminder(rid, 42)
    assert r is not None
    assert r.text == "Встреча с тимлидом"
    assert r.fire_at == fire_at
    assert r.advance_minutes == 5
    assert r.status == "pending"

    items = db.list_pending_reminders(42)
    assert len(items) == 1
    assert items[0].reminder_id == rid


def test_db_cancel_reminder(db: Database) -> None:
    db.upsert_user(42, "u", "User", None)
    rid = db.add_reminder(
        user_id=42,
        fire_at=int(time.time()) + 3600,
        advance_minutes=5,
        text="Test",
        source_note_id=None,
    )
    assert db.cancel_reminder(rid, 42) is True
    r = db.get_reminder(rid, 42)
    assert r is not None
    assert r.status == "cancelled"
    # повторная отмена возвращает False
    assert db.cancel_reminder(rid, 42) is False
    # не в pending list
    assert db.list_pending_reminders(42) == []


def test_db_mark_fired(db: Database) -> None:
    db.upsert_user(42, "u", "User", None)
    rid = db.add_reminder(
        user_id=42, fire_at=int(time.time()) + 3600, advance_minutes=0, text="X", source_note_id=None
    )
    db.mark_reminder_fired(rid)
    r = db.get_reminder(rid, 42)
    assert r is not None
    assert r.status == "fired"
    assert r.fired_at is not None


def test_db_user_isolation(db: Database) -> None:
    db.upsert_user(1, "a", "A", None)
    db.upsert_user(2, "b", "B", None)
    db.add_reminder(user_id=1, fire_at=int(time.time()) + 3600, advance_minutes=5, text="A1", source_note_id=None)
    db.add_reminder(user_id=2, fire_at=int(time.time()) + 3600, advance_minutes=5, text="B1", source_note_id=None)
    assert len(db.list_pending_reminders(1)) == 1
    assert len(db.list_pending_reminders(2)) == 1


def test_db_prune_old(db: Database) -> None:
    db.upsert_user(1, "a", "A", None)
    rid = db.add_reminder(user_id=1, fire_at=int(time.time()) - 3600, advance_minutes=0, text="old", source_note_id=None)
    db.mark_reminder_fired(rid)
    # cutoff в будущем → удалит
    n = db.prune_old_reminders(int(time.time()) + 1)
    assert n == 1


def test_materialize_skips_past(db: Database) -> None:
    db.upsert_user(1, "a", "A", None)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    specs = [
        ReminderSpec(what="прошлое", fire_at_iso=past, advance_minutes=5),
        ReminderSpec(what="будущее", fire_at_iso=future, advance_minutes=5),
        ReminderSpec(what="мусор", fire_at_iso="invalid", advance_minutes=5),
    ]
    out = materialize_reminders(db, 1, specs, "Europe/Moscow", 5, source_note_id=None)
    assert len(out) == 1
    assert out[0].text == "будущее"


def test_materialize_default_advance(db: Database) -> None:
    db.upsert_user(1, "a", "A", None)
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    # LLM не указал advance → падает в default_advance (10) из конфига
    spec = ReminderSpec(what="x", fire_at_iso=future)  # advance_minutes=None
    out = materialize_reminders(db, 1, [spec], "Europe/Moscow", 10, source_note_id=None)
    assert out[0].advance_minutes == 10
    # LLM указал явно → используется его значение
    spec2 = ReminderSpec(what="y", fire_at_iso=future, advance_minutes=30)
    out2 = materialize_reminders(db, 1, [spec2], "Europe/Moscow", 10, source_note_id=None)
    assert out2[0].advance_minutes == 30


def test_parse_offset_no_colon() -> None:
    """Regex fallback должен корректно парсить +0300, не считая его UTC."""
    from reminders import _parse_offset
    assert _parse_offset("+0300") == timezone(timedelta(hours=3))
    assert _parse_offset("-0530") == timezone(-timedelta(hours=5, minutes=30))
    assert _parse_offset("+03:00") == timezone(timedelta(hours=3))
    assert _parse_offset("Z") == timezone.utc
    assert _parse_offset("") is None
    assert _parse_offset("garbage") is None


def test_ical_export_basic() -> None:
    from db import Reminder
    r = Reminder(
        reminder_id=1, user_id=1, created_at=0, fire_at=1745672400,  # 2025-04-26 13:00 UTC
        advance_minutes=5, text="Test event", source_note_id=None, status="pending", fired_at=None,
    )
    ics = reminders_to_ical([r])
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "SUMMARY:Test event" in ics
    assert "TRIGGER:-PT5M" in ics
    assert "BEGIN:VALARM" in ics
    # CRLF endings
    assert "\r\n" in ics


def test_ical_escape_special_chars() -> None:
    from db import Reminder
    r = Reminder(
        reminder_id=1, user_id=1, created_at=0, fire_at=1745672400,
        advance_minutes=0, text="Test, with; chars\nand newline", source_note_id=None,
        status="pending", fired_at=None,
    )
    ics = reminders_to_ical([r])
    assert "Test\\, with\\; chars\\nand newline" in ics
    # без advance — VALARM не включаем
    assert "BEGIN:VALARM" not in ics


def test_ical_empty() -> None:
    ics = reminders_to_ical([])
    assert "BEGIN:VCALENDAR" in ics
    assert "END:VCALENDAR" in ics
    assert "BEGIN:VEVENT" not in ics
