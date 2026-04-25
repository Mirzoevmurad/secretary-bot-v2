from pathlib import Path

import pytest

from db import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "t.sqlite")


def _add(db: Database, user_id: int, title: str, transcript: str) -> int:
    return db.add_note(
        user_id=user_id,
        transcript=transcript,
        summary={"title": title, "summary": ["s1"], "tasks": [], "open_questions": [], "tags": [], "category": "Другое"},
        title=title,
        lang="ru",
        duration_seconds=42.0,
        audio_path=None,
        source="voice",
    )


def test_add_and_get(db):
    db.upsert_user(1, "a", "A", None)
    nid = _add(db, 1, "Тестовая", "привет это тест")
    assert nid > 0
    note = db.get_note(nid, 1)
    assert note is not None
    assert note.title == "Тестовая"
    assert note.transcript == "привет это тест"
    assert note.summary["category"] == "Другое"


def test_list_newest_first(db):
    db.upsert_user(1, None, None, None)
    a = _add(db, 1, "A", "aaa")
    b = _add(db, 1, "B", "bbb")
    listed = db.list_notes(1, limit=10)
    assert [n.note_id for n in listed] == [b, a]


def test_user_isolation(db):
    db.upsert_user(1, None, None, None)
    db.upsert_user(2, None, None, None)
    nid = _add(db, 1, "mine", "hi")
    assert db.get_note(nid, 2) is None  # чужому не видно
    assert db.get_note(nid, 1) is not None


def test_delete(db):
    db.upsert_user(1, None, None, None)
    nid = _add(db, 1, "x", "yyy")
    assert db.delete_note(nid, 1) is True
    assert db.get_note(nid, 1) is None
    assert db.delete_note(nid, 1) is False


def test_search_fts(db):
    db.upsert_user(1, None, None, None)
    _add(db, 1, "Митинг", "обсудили релиз мобильного приложения")
    _add(db, 1, "Покупки", "молоко хлеб яблоки")
    found = db.search(1, "релиз")
    assert len(found) == 1
    assert "релиз" in found[0].transcript
    none = db.search(1, "несуществующее-слово-xyz")
    assert none == []


def test_stats(db):
    db.upsert_user(1, None, None, None)
    _add(db, 1, "A", "aaa")
    _add(db, 1, "B", "bbb")
    s = db.stats(1)
    assert s["total"] == 2
    assert s["total_seconds"] == pytest.approx(84.0)


def test_set_lang(db):
    db.upsert_user(1, None, None, None)
    assert db.get_lang(1) == "auto"
    db.set_lang(1, "ru")
    assert db.get_lang(1) == "ru"


def test_forget_all(db):
    db.upsert_user(1, None, None, None)
    _add(db, 1, "A", "aaa")
    _add(db, 1, "B", "bbb")
    assert db.delete_all(1) == 2
    assert db.list_notes(1) == []
