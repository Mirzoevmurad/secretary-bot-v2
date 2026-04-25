"""Форматирование ответов бота."""
from __future__ import annotations

import html
from datetime import datetime, timezone

from db import Note
from llm import Summary


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def fmt_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    s = int(round(seconds))
    if s < 60:
        return f"{s}с"
    return f"{s // 60}м {s % 60:02d}с"


def fmt_created_at(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_category_emoji(cat: str) -> str:
    mapping = {
        "Работа": "💼",
        "Личное": "🏠",
        "Идея": "💡",
        "Встреча": "🤝",
        "Покупки": "🛒",
        "Учёба": "📚",
        "Здоровье": "💊",
        "Финансы": "💰",
        "Другое": "📝",
    }
    return mapping.get(cat, "📝")


def format_note(note: Note) -> str:
    s = note.summary
    title = s.get("title") or "Без заголовка"
    summary = s.get("summary") or []
    tasks = s.get("tasks") or []
    questions = s.get("open_questions") or []
    tags = s.get("tags") or []
    category = s.get("category") or "Другое"

    lines: list[str] = []
    lines.append(
        f"📝 <b>Заметка #{note.note_id}</b> · {esc(fmt_created_at(note.created_at))}"
    )
    meta_bits = [f"⏱ {fmt_duration(note.duration_seconds)}"]
    if note.lang:
        meta_bits.append(f"🌐 {esc(note.lang)}")
    meta_bits.append(f"{fmt_category_emoji(category)} {esc(category)}")
    lines.append(" · ".join(meta_bits))
    lines.append("")
    lines.append(f"<b>{esc(title)}</b>")

    if summary:
        lines.append("")
        lines.append("🔑 <b>Главное</b>")
        for item in summary:
            lines.append(f"• {esc(str(item))}")

    if tasks:
        lines.append("")
        lines.append("✅ <b>Задачи</b>")
        for i, t in enumerate(tasks, 1):
            what = esc(str(t.get("what", "")))
            who = t.get("who")
            when = t.get("when")
            suffix_parts = []
            if who:
                suffix_parts.append(f"👤 {esc(str(who))}")
            if when:
                suffix_parts.append(f"📅 {esc(str(when))}")
            suffix = (" — " + " · ".join(suffix_parts)) if suffix_parts else ""
            lines.append(f"{i}. {what}{suffix}")

    if questions:
        lines.append("")
        lines.append("❓ <b>Открытые вопросы</b>")
        for q in questions:
            lines.append(f"• {esc(str(q))}")

    if tags:
        lines.append("")
        lines.append(" ".join(f"#{esc(str(t)).replace(' ', '_')}" for t in tags))

    lines.append("")
    lines.append(f"📄 Транскрипт: /get_{note.note_id}")
    return "\n".join(lines)


def format_list(notes: list[Note]) -> str:
    if not notes:
        return "📭 Заметок пока нет. Пришлите голосовое — я обработаю."
    lines = ["🗂 <b>Последние заметки</b>:"]
    for n in notes:
        emoji = fmt_category_emoji(n.summary.get("category", "Другое"))
        title = n.summary.get("title") or n.transcript[:50]
        lines.append(
            f"{emoji} <b>#{n.note_id}</b> · {esc(fmt_created_at(n.created_at))} · {esc(title)}"
        )
    lines.append("")
    lines.append("Открыть: /get_&lt;id&gt;  ·  Удалить: /delete_&lt;id&gt;")
    return "\n".join(lines)


def format_search(notes: list[Note], query: str) -> str:
    if not notes:
        return f"🔍 Ничего не нашёл по запросу «{esc(query)}»."
    lines = [f"🔍 Нашёл {len(notes)} заметок по «{esc(query)}»:"]
    for n in notes:
        title = n.summary.get("title") or n.transcript[:50]
        lines.append(
            f"• <b>#{n.note_id}</b> · {esc(fmt_created_at(n.created_at))} · {esc(title)}"
        )
    return "\n".join(lines)


def format_transcript(note: Note) -> str:
    title = note.summary.get("title") or "Без заголовка"
    return (
        f"📄 <b>Транскрипт #{note.note_id}</b>\n"
        f"<i>{esc(title)}</i>\n\n"
        f"{esc(note.transcript)}"
    )


def format_stats(stats: dict) -> str:
    total = stats.get("total", 0)
    secs = stats.get("total_seconds", 0.0)
    lines = [
        f"📊 <b>Ваша статистика</b>",
        f"• Всего заметок: <b>{total}</b>",
        f"• Суммарная длительность: <b>{fmt_duration(secs)}</b>",
    ]
    if stats.get("first_at"):
        lines.append(f"• Первая: {fmt_created_at(stats['first_at'])}")
    if stats.get("last_at"):
        lines.append(f"• Последняя: {fmt_created_at(stats['last_at'])}")
    return "\n".join(lines)


def format_help() -> str:
    return (
        "🤖 <b>Бот-секретарь</b>\n"
        "Пришлите голосовое сообщение или аудиофайл — я сделаю из него структурированную заметку: заголовок, саммари, задачи, теги.\n\n"
        "<b>Команды</b>\n"
        "/start — приветствие\n"
        "/last — повторить последнюю заметку\n"
        "/list — список последних 10 заметок\n"
        "/search &lt;текст&gt; — поиск по всем заметкам\n"
        "/stats — ваша статистика\n"
        "/lang ru|en|auto — язык распознавания по умолчанию\n"
        "/get_&lt;id&gt; — транскрипт заметки\n"
        "/delete_&lt;id&gt; — удалить заметку\n"
        "/forget_all — удалить все заметки\n"
        "/help — эта справка"
    )


def format_processing(note_id_hint: str = "") -> str:
    return "🎧 Слушаю… (распознаю речь и структурирую)"


def format_welcome() -> str:
    return (
        "👋 Привет! Я бот-секретарь.\n\n"
        "Пришлите мне голосовое сообщение — я распознаю речь и соберу структурированную заметку с саммари, задачами и тегами.\n\n"
        "Команды: /help"
    )


def format_summary_only(s: Summary) -> str:
    """Краткое форматирование без хедера/даты — для предпросмотра."""
    note_like = Note(
        note_id=0,
        user_id=0,
        created_at=0,
        duration_seconds=None,
        lang=None,
        title=s.title,
        transcript="",
        summary=s.model_dump(),
        audio_path=None,
        source="voice",
    )
    return format_note(note_like)
