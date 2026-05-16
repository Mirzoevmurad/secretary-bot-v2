"""Форматирование ответов бота."""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from db import Reminder


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


def format_help() -> str:
    return (
        "🤖 <b>Бот-секретарь</b>\n"
        "Пришлите <b>голосовое</b> или <b>текст</b> — распознаю, отполирую и при необходимости поставлю напоминание.\n\n"
        "<b>Напоминания</b>\n"
        "/reminders — активные напоминания\n"
        "/cancel_&lt;id&gt; — отменить напоминание\n"
        "/export_ical — выгрузить все активные напоминания файлом .ics (импортируется в любой календарь)\n"
        "\n<b>Спецрежимы</b> (срабатывают по ключевым словам в начале сообщения)\n"
        "🌍 «<b>переведи</b> ...» / «<b>нужен перевод</b>» — перевод RU↔EN с кнопками копирования.\n"
        "🤖 «<b>Грок</b> ...» — обычный AI-чат: «Грок что такое дискриминант», «Грок объясни git rebase».\n"
        "\n<b>Прочее</b>\n"
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
        "Пришлите <b>голосовое</b> или <b>текстовое</b> сообщение — распознаю, "
        "отполирую и верну чистый текст.\n\n"
        "Если в речи есть «завтра в 12:30 встреча с тимлидом, напомни за 5 минут» — "
        "автоматически поставлю напоминание и пришлю уведомление вовремя.\n\n"
        "Команды: /help"
    )
