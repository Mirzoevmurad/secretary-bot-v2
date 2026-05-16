from pathlib import Path

import pytest

from db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "t.sqlite")


def test_set_lang(db):
    db.upsert_user(1, None, None, None)
    assert db.get_lang(1) == "auto"
    db.set_lang(1, "ru")
    assert db.get_lang(1) == "ru"


def test_update_reminder_text_and_time(db):
    db.upsert_user(1, None, None, None)
    rid = db.add_reminder(user_id=1, fire_at=10**10, advance_minutes=5, text="старый", source_note_id=None)
    assert db.update_reminder_text(rid, 1, "новый") is True
    assert db.get_reminder(rid, 1).text == "новый"
    assert db.update_reminder_fire_at(rid, 1, 10**10 + 100) is True
    assert db.get_reminder(rid, 1).fire_at == 10**10 + 100
    # cancelled — нельзя менять
    db.cancel_reminder(rid, 1)
    assert db.update_reminder_text(rid, 1, "после отмены") is False
