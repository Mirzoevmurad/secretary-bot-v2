from db import Note
from formatter import format_note, format_list, format_search


def _note(note_id: int = 1, **overrides) -> Note:
    summary = {
        "title": "Тест заметки",
        "summary": ["пункт один", "пункт два"],
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
    assert "Главное" in out and "пункт один" in out
    assert "Задачи" in out and "сделать отчёт" in out and "👤 я" in out and "📅 завтра" in out
    assert "Открытые вопросы" in out and "когда релиз?" in out
    assert "#работа" in out and "#отчёт" in out
    assert "/get_1" in out


def test_format_note_escapes_html():
    n = _note(summary_overrides={"title": "<script>", "summary": ["<b>x</b>"], "tasks": [], "open_questions": [], "tags": []})
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
