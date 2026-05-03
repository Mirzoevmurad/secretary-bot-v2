"""Форматирование ответов бота."""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from db import Note, Reminder
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


def fmt_created_at(ts: int, tz_name: str | None = None) -> str:
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            tz = None
    else:
        tz = None
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt = dt.astimezone(tz) if tz else dt.astimezone()
    return dt.strftime("%d.%m.%Y %H:%M")


def fmt_fire_at(ts: int, tz_name: str | None = None) -> str:
    """Время в локальной TZ + относительное «через X»."""
    try:
        tz = ZoneInfo(tz_name) if tz_name else None
    except Exception:  # noqa: BLE001
        tz = None
    dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt = dt_utc.astimezone(tz) if tz else dt_utc.astimezone()
    abs_str = dt.strftime("%d.%m.%Y %H:%M")
    delta = ts - int(datetime.now(tz=timezone.utc).timestamp())
    if delta < 0:
        return f"{abs_str} (прошло)"
    if delta < 60:
        rel = "меньше минуты"
    elif delta < 3600:
        rel = f"через {delta // 60}м"
    elif delta < 86400:
        h = delta // 3600
        m = (delta % 3600) // 60
        rel = f"через {h}ч {m}м" if m else f"через {h}ч"
    else:
        d = delta // 86400
        h = (delta % 86400) // 3600
        rel = f"через {d}д {h}ч" if h else f"через {d}д"
    return f"{abs_str} ({rel})"


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


def format_note(note: Note, *, tz_name: str | None = None, scheduled_reminders: list[Reminder] | None = None) -> str:
    s = note.summary
    title = s.get("title") or "Без заголовка"
    details = s.get("details") or ""
    tasks = s.get("tasks") or []
    questions = s.get("open_questions") or []
    decisions = s.get("decisions") or []
    tags = s.get("tags") or []
    category = s.get("category") or "Другое"

    lines: list[str] = []
    source = (note.source or "voice")
    src_emoji = "📝" if source == "text" else "🎙"
    lines.append(
        f"{src_emoji} <b>Заметка #{note.note_id}</b> · {esc(fmt_created_at(note.created_at, tz_name))}"
    )
    meta_bits: list[str] = []
    if note.duration_seconds:
        meta_bits.append(f"⏱ {fmt_duration(note.duration_seconds)}")
    if note.lang:
        meta_bits.append(f"🌐 {esc(note.lang)}")
    meta_bits.append(f"{fmt_category_emoji(category)} {esc(category)}")
    lines.append(" · ".join(meta_bits))
    lines.append("")
    lines.append(f"<b>{esc(title)}</b>")

    if details:
        lines.append("")
        lines.append(esc(details))

    if decisions:
        lines.append("")
        lines.append("⚖️ <b>Решения</b>")
        for d in decisions:
            lines.append(f"• {esc(str(d))}")

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

    if scheduled_reminders:
        lines.append("")
        lines.append("🔔 <b>Напоминания</b>")
        for r in scheduled_reminders:
            adv = f", за {r.advance_minutes}м" if r.advance_minutes else ""
            lines.append(
                f"• <b>#{r.reminder_id}</b> · {esc(fmt_fire_at(r.fire_at, tz_name))}{adv} — {esc(r.text)}"
            )

    if tags:
        lines.append("")
        lines.append(" ".join(f"#{esc(str(t)).replace(' ', '_')}" for t in tags))

    lines.append("")
    lines.append(f"📄 Транскрипт: /get_{note.note_id}")
    return "\n".join(lines)


def format_reminders_list(reminders: list[Reminder], tz_name: str | None = None) -> str:
    if not reminders:
        return (
            "🔕 Напоминаний нет.\n\n"
            "Запишите голосовое или пришлите текст — что-то вроде "
            "<i>«завтра в 12:30 встреча с тимлидом, напомни за 5 минут»</i>."
        )
    lines = [f"🔔 <b>Активные напоминания</b> ({len(reminders)})"]
    for r in reminders:
        adv = f" · за {r.advance_minutes}м" if r.advance_minutes else ""
        lines.append("")
        lines.append(f"<b>#{r.reminder_id}</b> · {esc(fmt_fire_at(r.fire_at, tz_name))}{adv}")
        lines.append(esc(r.text))
        lines.append(f"<i>Отменить: /cancel_{r.reminder_id}</i>")
    return "\n".join(lines)


def format_reminder_advance(reminder: Reminder, tz_name: str | None = None) -> str:
    return (
        f"⏰ <b>Скоро напоминание</b>\n"
        f"<i>{esc(fmt_fire_at(reminder.fire_at, tz_name))}</i>\n\n"
        f"<b>{esc(reminder.text)}</b>"
    )


def format_reminder_fire(reminder: Reminder, tz_name: str | None = None) -> str:
    return (
        f"🔔 <b>Сейчас!</b>\n\n"
        f"<b>{esc(reminder.text)}</b>\n\n"
        f"<i>Время: {esc(fmt_fire_at(reminder.fire_at, tz_name))}</i>"
    )


def format_list(notes: list[Note], tz_name: str | None = None) -> str:
    if not notes:
        return "📭 Заметок пока нет. Пришлите голосовое — я обработаю."
    lines = ["🗂 <b>Последние заметки</b>:"]
    for n in notes:
        emoji = fmt_category_emoji(n.summary.get("category", "Другое"))
        title = n.summary.get("title") or n.transcript[:50]
        lines.append(
            f"{emoji} <b>#{n.note_id}</b> · {esc(fmt_created_at(n.created_at, tz_name))} · {esc(title)}"
        )
    lines.append("")
    lines.append("Открыть: /get_&lt;id&gt;  ·  Удалить: /delete_&lt;id&gt;")
    return "\n".join(lines)


def format_search(notes: list[Note], query: str, tz_name: str | None = None) -> str:
    if not notes:
        return f"🔍 Ничего не нашёл по запросу «{esc(query)}»."
    lines = [f"🔍 Нашёл {len(notes)} заметок по «{esc(query)}»:"]
    for n in notes:
        title = n.summary.get("title") or n.transcript[:50]
        lines.append(
            f"• <b>#{n.note_id}</b> · {esc(fmt_created_at(n.created_at, tz_name))} · {esc(title)}"
        )
    return "\n".join(lines)


def format_transcript(note: Note) -> str:
    title = note.summary.get("title") or "Без заголовка"
    return (
        f"📄 <b>Транскрипт #{note.note_id}</b>\n"
        f"<i>{esc(title)}</i>\n\n"
        f"{esc(note.transcript)}"
    )


def format_stats(stats: dict, tz_name: str | None = None) -> str:
    total = stats.get("total", 0)
    secs = stats.get("total_seconds", 0.0)
    lines = [
        f"📊 <b>Ваша статистика</b>",
        f"• Всего заметок: <b>{total}</b>",
        f"• Суммарная длительность: <b>{fmt_duration(secs)}</b>",
    ]
    if stats.get("first_at"):
        lines.append(f"• Первая: {fmt_created_at(stats['first_at'], tz_name)}")
    if stats.get("last_at"):
        lines.append(f"• Последняя: {fmt_created_at(stats['last_at'], tz_name)}")
    return "\n".join(lines)


def format_help() -> str:
    return (
        "🤖 <b>Бот-секретарь</b>\n"
        "Пришлите <b>голосовое</b> или <b>текст</b> — соберу структурированную заметку: "
        "заголовок, саммари, задачи, теги. Если в речи есть «напомни в Y часов» — поставлю напоминание.\n\n"
        "<b>Заметки</b>\n"
        "/last — повторить последнюю заметку\n"
        "/list — список последних 10 заметок\n"
        "/search &lt;текст&gt; — поиск по всем заметкам\n"
        "/get_&lt;id&gt; — транскрипт заметки\n"
        "/delete_&lt;id&gt; — удалить заметку\n"
        "/txt [id] — полированный читаемый текст последней заметки (или #id)\n"
        "/forget_all — удалить все заметки\n"
        "\n<b>Напоминания</b>\n"
        "/reminders — активные напоминания\n"
        "/cancel_&lt;id&gt; — отменить напоминание\n"
        "/export_ical — выгрузить все активные напоминания файлом .ics (импортируется в любой календарь)\n"
        "\n<b>Прочее</b>\n"
        "/stats — ваша статистика\n"
        "/lang ru|en|auto — язык распознавания\n"
        "/help — эта справка"
    )


def format_processing(note_id_hint: str = "") -> str:
    return "🎧 Слушаю… (распознаю речь и структурирую)"


def format_processing_text() -> str:
    return "🧠 Обрабатываю текст…"


def format_welcome() -> str:
    return (
        "👋 Привет! Я бот-секретарь.\n\n"
        "Пришлите <b>голосовое</b> или <b>текстовое</b> сообщение — соберу из него "
        "структурированную заметку с саммари, задачами и тегами.\n\n"
        "Если в речи есть «завтра в 12:30 встреча с тимлидом, напомни за 5 минут» — "
        "автоматически поставлю напоминание и пришлю уведомление вовремя.\n\n"
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
