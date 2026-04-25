"""Тесты для _split_for_telegram: HTML-безопасное дробление длинных сообщений."""
from __future__ import annotations

import os
import re

os.environ.setdefault("SECRETARY_BOT_TOKEN", "x")
os.environ.setdefault("GROQ_API_KEY", "x")
os.environ.setdefault("ALLOWED_USER_IDS", "1")

from bot import _split_for_telegram, _has_unsafe_chunk


def test_short_text_one_chunk():
    text = "hello world"
    assert _split_for_telegram(text, limit=100) == ["hello world"]


def test_split_by_paragraphs():
    blocks = ["A" * 80] * 5
    text = "\n\n".join(blocks)
    chunks = _split_for_telegram(text, limit=200)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 200


def test_split_preserves_html_tags():
    """`<b>...</b>` помещается в одну строку — после split тэги не разорваны."""
    blocks = []
    for i in range(20):
        blocks.append(f"<b>Заголовок {i}</b>\nКонтент длиной около пятидесяти символов здесь.")
    text = "\n\n".join(blocks)
    chunks = _split_for_telegram(text, limit=400)
    for c in chunks:
        # количество <b> и </b> должно совпадать в каждом куске
        assert c.count("<b>") == c.count("</b>"), f"unbalanced <b>: {c!r}"
        assert c.count("<i>") == c.count("</i>"), f"unbalanced <i>: {c!r}"


def test_long_paragraph_splits_at_lines():
    # один блок без \n\n, но с \n
    lines = [f"Строка {i} " * 5 for i in range(30)]
    text = "\n".join(lines)
    chunks = _split_for_telegram(text, limit=300)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 300


def test_long_line_splits_at_sentences():
    # одна строка без \n, но с предложениями
    sent = "Это одно предложение про текст. " * 50
    chunks = _split_for_telegram(sent, limit=300)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 300


def test_sentence_split_preserves_spacing():
    """При склейке предложений пробел между ними не должен теряться."""
    s = "Первое предложение про что-то очень важное и нужное. " * 20
    chunks = _split_for_telegram(s, limit=300)
    rejoined = " ".join(chunks)
    # ни в одном из кусков не должно быть склейки `.П` (точка вплотную к заглавной)
    for c in chunks:
        assert ".П" not in c, f"sentences glued without space: {c!r}"
    # суммарно текст восстановим (с точностью до пробелов на границах кусков)
    normalized_orig = " ".join(s.split())
    normalized_join = " ".join(rejoined.split())
    assert normalized_orig.strip() == normalized_join.strip()


def test_safe_flag_with_normal_text():
    text = "обычный\n\nтекст " * 100
    chunks = _split_for_telegram(text, limit=500)
    assert not _has_unsafe_chunk(chunks, 500)


def test_real_note_format():
    """Реалистичная заметка с полным форматированием — не должна ломать тэги."""
    note_text = (
        "📝 <b>Заметка #1</b> · 25.04.2026 14:33\n"
        "⏱ 3м 42с · 🌐 ru · 💼 Работа\n"
        "\n"
        "<b>Архитектура аналитического Telegram-бота</b>\n"
        "\n"
        "<i>Один абзац-описание сути заметки на пятнадцать-двадцать слов с контекстом.</i>\n"
        "\n"
        "📖 <b>Подробно</b>\n"
        + ("Длинный детальный пересказ заметки. " * 100) + "\n"
        "\n"
        "🔑 <b>Главное</b>\n"
        + "\n".join(f"• Развёрнутый пункт {i} с контекстом и аргументами." for i in range(8)) + "\n"
        "\n"
        "📄 Транскрипт: /get_1"
    )
    chunks = _split_for_telegram(note_text, limit=3900)
    for c in chunks:
        assert c.count("<b>") == c.count("</b>"), f"unbalanced <b>: chunk={c!r}"
        assert c.count("<i>") == c.count("</i>"), f"unbalanced <i>: chunk={c!r}"
        assert len(c) <= 3900
