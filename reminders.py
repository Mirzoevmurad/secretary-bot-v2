"""Логика напоминаний: парсинг ISO-дат, JobQueue-планирование, iCal-экспорт."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from db import Database, Reminder
from llm import ReminderSpec


logger = logging.getLogger(__name__)


def now_context_block(tz_name: str) -> str:
    """Префикс, который мы передаём LLM, чтобы он мог преобразовать «завтра в 12:30» в абсолютное время."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
    now = datetime.now(tz=tz)
    weekday = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][now.weekday()]
    return (
        f"Текущее время автора: {now.strftime('%Y-%m-%d %H:%M:%S %z')} ({weekday}).\n"
        f"Часовой пояс автора: {tz_name}.\n"
        f"При создании fire_at_iso используй именно этот часовой пояс."
    )


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:?\d{2})?$"
)


def _parse_offset(tz_str: str) -> timezone | None:
    """Парсит '+0300', '+03:00', '-0530', 'Z' в datetime.timezone."""
    if not tz_str:
        return None
    if tz_str == "Z":
        return timezone.utc
    sign = 1 if tz_str[0] == "+" else -1
    rest = tz_str[1:].replace(":", "")
    if len(rest) != 4 or not rest.isdigit():
        return None
    hh, mm = int(rest[:2]), int(rest[2:])
    return timezone(sign * timedelta(hours=hh, minutes=mm))


def parse_iso_to_epoch(iso: str, default_tz: str) -> int | None:
    """Преобразует ISO 8601 строку в epoch (UTC seconds). Возвращает None, если не парсится."""
    if not iso:
        return None
    iso = iso.strip()
    # python's fromisoformat в 3.11+ хорошо ест почти всё
    try:
        # 'Z' suffix → '+00:00'
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(default_tz))
            except Exception:  # noqa: BLE001
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.astimezone(timezone.utc).timestamp())
    except (ValueError, TypeError):
        # fallback на регулярку (для Python 3.10 и форматов вроде +0300 без двоеточия)
        m = _ISO_RE.match(iso)
        if not m:
            return None
        y, mo, d, hh, mm, ss, tz = m.groups()
        try:
            if tz:
                tzinfo: timezone | ZoneInfo | None = _parse_offset(tz)
                if tzinfo is None:
                    return None
            else:
                try:
                    tzinfo = ZoneInfo(default_tz)
                except Exception:  # noqa: BLE001
                    tzinfo = timezone.utc
            dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), tzinfo=tzinfo)
            return int(dt.astimezone(timezone.utc).timestamp())
        except (ValueError, TypeError):
            return None


def materialize_reminders(
    db: Database,
    user_id: int,
    specs: list[ReminderSpec],
    tz_name: str,
    default_advance: int,
    source_note_id: int | None,
) -> list[Reminder]:
    """Превращает LLM-объекты в записи в БД. Отбрасывает напоминания в прошлом."""
    out: list[Reminder] = []
    now = int(datetime.now(tz=timezone.utc).timestamp())
    for spec in specs:
        fire_at = parse_iso_to_epoch(spec.fire_at_iso, tz_name)
        if fire_at is None:
            logger.warning("reminder skipped: bad iso=%r", spec.fire_at_iso)
            continue
        if fire_at <= now:
            logger.warning("reminder skipped: in the past iso=%r", spec.fire_at_iso)
            continue
        adv = spec.advance_minutes if spec.advance_minutes is not None else default_advance
        rid = db.add_reminder(
            user_id=user_id,
            fire_at=fire_at,
            advance_minutes=max(0, int(adv)),
            text=spec.what,
            source_note_id=source_note_id,
        )
        r = db.get_reminder(rid, user_id)
        if r:
            out.append(r)
    return out


# ---- iCal --------------------------------------------------------------

def _ical_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _ical_dt(epoch: int) -> str:
    """UTC datetime в формате iCal: 20260426T093000Z"""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def reminders_to_ical(reminders: list[Reminder], calendar_name: str = "secretary-bot") -> str:
    """Генерирует .ics файл с VEVENT на каждое напоминание + VALARM для предупреждения."""
    now = _ical_dt(int(datetime.now(tz=timezone.utc).timestamp()))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//secretary-bot//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_escape(calendar_name)}",
    ]
    for r in reminders:
        uid = f"reminder-{r.reminder_id}@secretary-bot"
        # длительность события — 30 минут по умолчанию
        end = r.fire_at + 30 * 60
        ev_lines = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART:{_ical_dt(r.fire_at)}",
            f"DTEND:{_ical_dt(end)}",
            f"SUMMARY:{_ical_escape(r.text)}",
        ]
        if r.advance_minutes > 0:
            ev_lines.extend([
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_ical_escape(r.text)}",
                f"TRIGGER:-PT{r.advance_minutes}M",
                "END:VALARM",
            ])
        ev_lines.append("END:VEVENT")
        lines.extend(ev_lines)
    lines.append("END:VCALENDAR")
    # iCal требует CRLF
    return "\r\n".join(lines) + "\r\n"
