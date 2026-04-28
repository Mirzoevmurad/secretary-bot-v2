"""Secretary-bot: Telegram голосовой бот-секретарь поверх Groq Whisper + LLM."""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

from telegram import BotCommand, ForceReply, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    filters,
)

from config import Config
from db import Database, Note, Reminder
from llm import GroqLLM, LLMError, Summary
from reminders import (
    materialize_reminders,
    now_context_block,
    parse_iso_to_epoch,
    reminders_to_ical,
)
from stt import GroqSTT, STTError
import formatter as fmt
import keyboards as kb


try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


logger = logging.getLogger("secretary")


# ---- helpers -----------------------------------------------------------


def _is_allowed(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    cfg: Config = context.bot_data["cfg"]
    return user_id in cfg.allowed_user_ids


async def _deny(update: Update) -> None:
    await update.effective_message.reply_text(
        "Этот бот приватный. Обратитесь к владельцу."
    )


def _split_for_telegram(text: str, limit: int = 3900) -> list[str]:
    """Делит длинное HTML-сообщение на куски ≤ limit символов, не ломая HTML-тэги.

    `format_note` использует только теги `<b>` и `<i>`, и они никогда не пересекают
    `\\n`. Поэтому любая граница `\\n\\n` / `\\n` безопасна. Сначала пытаемся резать
    по абзацам, потом по строкам, потом по предложениям/словам.
    """
    if len(text) <= limit:
        return [text]

    def _group(items: list[str], sep: str) -> list[str]:
        """Группирует элементы в строки ≤ limit, сохраняя `sep` между ними."""
        out: list[str] = []
        buf: list[str] = []
        cur = 0
        sep_len = len(sep)
        for it in items:
            it_len = len(it) + (sep_len if buf else 0)
            if cur + it_len > limit and buf:
                out.append(sep.join(buf))
                buf = [it]
                cur = len(it)
            else:
                buf.append(it)
                cur += it_len
        if buf:
            out.append(sep.join(buf))
        return out

    # Шаг 1: пытаемся резать по абзацам (\n\n).
    chunks = _group(text.split("\n\n"), "\n\n")

    # Шаг 2: если какой-то кусок всё ещё длиннее limit — режем его по строкам.
    refined: list[str] = []
    for c in chunks:
        if len(c) <= limit:
            refined.append(c)
            continue
        refined.extend(_group(c.split("\n"), "\n"))
    chunks = refined

    # Шаг 3: если строка длиннее limit — режем по предложениям, потом по словам.
    refined = []
    for c in chunks:
        if len(c) <= limit:
            refined.append(c)
            continue
        # делим по предложениям, сохраняя пробел при склейке (sep=" ")
        sentences = c.replace(". ", ".\x00").split("\x00")
        sub = _group(sentences, " ")
        # Шаг 3b: одно предложение длиннее limit — режем по словам.
        sub_refined: list[str] = []
        for s in sub:
            if len(s) <= limit:
                sub_refined.append(s)
                continue
            sub_refined.extend(_group(s.split(" "), " "))
        refined.extend(sub_refined)
    chunks = refined

    # Финальная страховка: остался кусок > limit (нет ни пробелов, ни границ).
    # Такого не должно быть с естественным текстом, но если случилось — режем
    # тупо по символам и шлём как plain text (без HTML), чтобы не разорвать тэг.
    return chunks


def _has_unsafe_chunk(chunks: list[str], limit: int = 3900) -> bool:
    return any(len(c) > limit for c in chunks)


async def _reply_long_html(msg, text: str, limit: int = 3900, reply_markup=None) -> None:
    """Отправляет длинный HTML-текст одним или несколькими сообщениями.

    `reply_markup` (если задан) прикрепляется к ПОСЛЕДНЕМУ сообщению — иначе
    Telegram не позволит «разделить» клавиатуру между кусками.
    """
    chunks = _split_for_telegram(text, limit=limit)
    safe = not _has_unsafe_chunk(chunks, limit)
    if safe:
        for i, chunk in enumerate(chunks):
            kw = {"disable_web_page_preview": True}
            if i == len(chunks) - 1 and reply_markup is not None:
                kw["reply_markup"] = reply_markup
            await msg.reply_html(chunk, **kw)
        return
    # fallback: режем посимвольно и шлём как plain text. parse_mode=None обязателен.
    pieces = [text[i:i + limit] for i in range(0, len(text), limit)]
    for i, piece in enumerate(pieces):
        kw = {"parse_mode": None, "disable_web_page_preview": True}
        if i == len(pieces) - 1 and reply_markup is not None:
            kw["reply_markup"] = reply_markup
        await msg.reply_text(piece, **kw)


def _convert_to_wav(src: Path, dst: Path) -> None:
    """OGG/Opus → 16kHz mono WAV. Whisper в Groq API также принимает исходный ogg,
    но конвертация уменьшает размер и нормализует."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---- command handlers --------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(context, user.id):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    await update.effective_message.reply_html(fmt.format_welcome())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    await update.effective_message.reply_html(fmt.format_help())


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    note = db.last_note(update.effective_user.id)
    if note is None:
        await update.effective_message.reply_text("Заметок пока нет.")
        return
    await _reply_long_html(
        update.effective_message,
        fmt.format_note(note, tz_name=cfg.tz),
        reply_markup=kb.note_actions_kb(note.note_id),
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    limit = 10
    args = (context.args or [])
    if args and args[0].isdigit():
        limit = min(int(args[0]), 50)
    db: Database = context.bot_data["db"]
    cfg: Config = context.bot_data["cfg"]
    notes = db.list_notes(update.effective_user.id, limit=limit)
    markup = kb.note_list_kb([n.note_id for n in notes]) if notes else None
    await update.effective_message.reply_html(fmt.format_list(notes, cfg.tz), reply_markup=markup)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    q = " ".join(context.args or []).strip()
    if not q:
        await update.effective_message.reply_text("Использование: /search <запрос>")
        return
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    notes = db.search(update.effective_user.id, q, limit=10)
    await update.effective_message.reply_html(fmt.format_search(notes, q, cfg.tz))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    s = db.stats(update.effective_user.id)
    await update.effective_message.reply_html(fmt.format_stats(s, cfg.tz))


async def cmd_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    args = context.args or []
    if not args or args[0] not in {"ru", "en", "auto"}:
        current = context.bot_data["db"].get_lang(update.effective_user.id) or "auto"
        await update.effective_message.reply_text(
            f"Сейчас: {current}. Использование: /lang ru|en|auto"
        )
        return
    context.bot_data["db"].set_lang(update.effective_user.id, args[0])
    await update.effective_message.reply_text(f"Язык распознавания установлен: {args[0]}")


async def cmd_forget_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    n = db.delete_all(update.effective_user.id)
    await update.effective_message.reply_text(f"🗑 Удалено заметок: {n}")


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    items = db.list_pending_reminders(update.effective_user.id)
    markup = kb.reminders_list_kb([r.reminder_id for r in items]) if items else None
    await _reply_long_html(
        update.effective_message,
        fmt.format_reminders_list(items, cfg.tz),
        reply_markup=markup,
    )


async def cmd_export_ical(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    items = db.list_pending_reminders(update.effective_user.id)
    if not items:
        await update.effective_message.reply_text(
            "🔕 Активных напоминаний нет — экспортировать нечего."
        )
        return
    ics = reminders_to_ical(items, calendar_name=f"secretary-bot-{update.effective_user.id}")
    from io import BytesIO
    buf = BytesIO(ics.encode("utf-8"))
    buf.name = "reminders.ics"
    await update.effective_message.reply_document(
        document=buf,
        filename="reminders.ics",
        caption=(
            f"📅 {len(items)} напоминаний в формате iCalendar (.ics).\n"
            "Откройте файл на телефоне — система предложит добавить события в календарь."
        ),
    )


async def _send_polished(msg, context: ContextTypes.DEFAULT_TYPE, note: Note) -> None:
    """Полирует транскрипт заметки через LLM и отправляет в чат."""
    llm: GroqLLM = context.bot_data["llm"]
    placeholder = await msg.reply_text("📝 Полирую…")
    try:
        polished = await llm.polish(note.transcript)
    except LLMError as e:
        await placeholder.edit_text(f"⚠️ Не смог полировать: {e}")
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("polish failed")
        await placeholder.edit_text(f"⚠️ Ошибка полировки ({type(e).__name__}).")
        return
    try:
        await placeholder.delete()
    except Exception:  # noqa: BLE001
        pass
    header = f"<b>📝 Полированный текст #{note.note_id}</b>\n\n"
    body = fmt.esc(polished)
    await _reply_long_html(msg, header + body)


async def cmd_txt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/txt` — полирует транскрипт последней заметки.
    `/txt N` — полирует заметку #N.
    """
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    args = context.args or []
    note: Note | None
    if args and args[0].isdigit():
        note = db.get_note(int(args[0]), update.effective_user.id)
        if note is None:
            await update.effective_message.reply_text(f"Заметка #{args[0]} не найдена.")
            return
    else:
        note = db.last_note(update.effective_user.id)
        if note is None:
            await update.effective_message.reply_text(
                "Заметок пока нет — пришлите голосовое или текст."
            )
            return
    await _send_polished(update.effective_message, context, note)


# --- /get_N и /delete_N как динамические команды -----------------------


_GET_RE = re.compile(r"^/get_(\d+)(?:@\w+)?$")
_DELETE_RE = re.compile(r"^/delete_(\d+)(?:@\w+)?$")
_CANCEL_RE = re.compile(r"^/cancel_(\d+)(?:@\w+)?$")

MIN_TEXT_NOTE_LEN = 3


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает любые текстовые сообщения, включая динамические команды /get_/delete_/cancel_.

    Если это плоский текст без команд — обрабатываем как заметку (тот же пайплайн, что у голоса,
    только без STT).
    """
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    txt = (update.effective_message.text or "").strip()
    if not txt:
        return
    db: Database = context.bot_data["db"]

    # Если ждём ответ на edit-запрос — обработать первым делом, до /-команд и LLM.
    # Принимаем как явный reply на ForceReply-промпт, так и любое следующее НЕ-команд-
    # ное сообщение (UI обещает оба варианта). /команды не перехватываем, чтобы юзер
    # мог отменить edit любой командой типа /reminders или /list.
    pending = context.user_data.get("pending_edit") if context.user_data is not None else None
    if pending:
        is_reply_to_prompt = (
            update.effective_message.reply_to_message is not None
            and update.effective_message.reply_to_message.message_id == pending.get("prompt_id")
        )
        if is_reply_to_prompt or not txt.startswith("/"):
            await _apply_pending_edit(update, context, pending, txt)
            return
        # /-команда при активном pending_edit — снимаем pending и пропускаем команду дальше,
        # чтобы edit не «прилип» к следующему случайному тексту в будущем.
        if context.user_data is not None:
            context.user_data.pop("pending_edit", None)

    m = _GET_RE.match(txt)
    if m:
        note_id = int(m.group(1))
        note = db.get_note(note_id, update.effective_user.id)
        if note is None:
            await update.effective_message.reply_text(f"Заметка #{note_id} не найдена.")
            return
        await _reply_long_html(
            update.effective_message,
            fmt.format_transcript(note),
            reply_markup=kb.note_actions_kb(note.note_id),
        )
        return
    m = _DELETE_RE.match(txt)
    if m:
        note_id = int(m.group(1))
        ok = db.delete_note(note_id, update.effective_user.id)
        await update.effective_message.reply_text(
            f"🗑 Заметка #{note_id} удалена." if ok else f"Заметка #{note_id} не найдена."
        )
        return
    m = _CANCEL_RE.match(txt)
    if m:
        rid = int(m.group(1))
        ok = db.cancel_reminder(rid, update.effective_user.id)
        if ok:
            _cancel_reminder_jobs(context.application, rid)
            await update.effective_message.reply_text(f"🗑 Напоминание #{rid} отменено.")
        else:
            await update.effective_message.reply_text(f"Напоминание #{rid} не найдено или уже отменено.")
        return

    # неизвестная /-команда → справка
    if txt.startswith("/"):
        await update.effective_message.reply_text(
            "Неизвестная команда. /help — список команд."
        )
        return

    # плоский текст → пропускаем через тот же пайплайн, что и голос (но без STT)
    if len(txt) < MIN_TEXT_NOTE_LEN:
        await update.effective_message.reply_text(
            "Текст слишком короткий. Пришлите хотя бы пару предложений или голосовое 🎤."
        )
        return

    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name, user.last_name)
    placeholder = await update.effective_message.reply_text(fmt.format_processing_text())
    await context.bot.send_chat_action(
        chat_id=update.effective_message.chat_id, action=ChatAction.TYPING
    )
    await _process_summary(
        update,
        context,
        placeholder=placeholder,
        transcript=txt,
        lang=None,
        duration=None,
        source="text",
        audio_path_saved=None,
    )


# ---- inline-button callbacks + edit flow ------------------------------


_FIELD_PROMPTS = {
    "title": "Пришлите новый заголовок для заметки",
    "cat": "Пришлите новую категорию (например: Работа, Личное, Идея, Покупки)",
    "tags": "Пришлите теги через пробел или запятую (например: проект, дедлайн)",
    "rtext": "Пришлите новый текст напоминания",
    "rtime": (
        "Пришлите новое время напоминания. Можно:\n"
        "• ISO: 2026-04-26 14:30 (в TZ Europe/Moscow)\n"
        "• Свободно: «завтра в 12:30», «через 2 часа», «в пятницу в 9»"
    ),
}


async def _ask_for_edit(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    kind: str,
    item_id: int,
    field: str,
) -> None:
    prompt_text = _FIELD_PROMPTS.get(field, "Пришлите новое значение")
    prompt = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✏️ {prompt_text}\n\n"
            "<i>Можно ответить текстом или голосовым 🎤 — следующее сообщение применится.</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=ForceReply(selective=True, input_field_placeholder="новое значение…"),
    )
    if context.user_data is None:
        return
    context.user_data["pending_edit"] = {
        "kind": kind,
        "id": item_id,
        "field": field,
        "prompt_id": prompt.message_id,
    }


async def _apply_pending_edit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pending: dict,
    new_value: str,
) -> None:
    """Применяет pending edit (заметка/напоминание × поле). Снимает pending в любом случае."""
    db: Database = context.bot_data["db"]
    cfg: Config = context.bot_data["cfg"]
    user_id = update.effective_user.id
    kind, item_id, field = pending["kind"], int(pending["id"]), pending["field"]
    new_value = new_value.strip()
    # Очищаем pending до возможного fail — иначе застрянем.
    if context.user_data is not None:
        context.user_data.pop("pending_edit", None)

    if not new_value:
        await update.effective_message.reply_text("Пустое значение, отмена.")
        return

    if kind == "note":
        if field == "title":
            ok = db.update_note_title(item_id, user_id, new_value[:200])
            await update.effective_message.reply_text(
                f"✅ Заголовок заметки #{item_id} обновлён." if ok else f"Заметка #{item_id} не найдена."
            )
        elif field == "cat":
            ok = db.update_note_summary_field(item_id, user_id, "category", new_value[:50])
            await update.effective_message.reply_text(
                f"✅ Категория заметки #{item_id} обновлена." if ok else f"Заметка #{item_id} не найдена."
            )
        elif field == "tags":
            tags = [t.strip() for t in re.split(r"[,\s]+", new_value) if t.strip()][:20]
            ok = db.update_note_summary_field(item_id, user_id, "tags", tags)
            await update.effective_message.reply_text(
                f"✅ Теги заметки #{item_id}: {', '.join(tags) or '—'}" if ok else f"Заметка #{item_id} не найдена."
            )
        else:
            await update.effective_message.reply_text("Неизвестное поле.")
        return

    if kind == "rem":
        rem = db.get_reminder(item_id, user_id)
        if rem is None:
            await update.effective_message.reply_text(f"Напоминание #{item_id} не найдено.")
            return
        if rem.status != "pending":
            await update.effective_message.reply_text(
                f"Напоминание #{item_id} уже {rem.status}, его нельзя менять."
            )
            return
        if field == "rtext":
            ok = db.update_reminder_text(item_id, user_id, new_value[:200])
            await update.effective_message.reply_text(
                f"✅ Текст напоминания #{item_id} обновлён." if ok else "Не удалось обновить."
            )
        elif field == "rtime":
            new_epoch = await _parse_new_fire_at(context, new_value, cfg.tz)
            if new_epoch is None:
                await update.effective_message.reply_text(
                    "Не понял дату/время. Попробуйте «завтра в 14:30» или «через 1 час»."
                )
                return
            now = int(time.time())
            if new_epoch <= now:
                await update.effective_message.reply_text("Время в прошлом. Отмена.")
                return
            ok = db.update_reminder_fire_at(item_id, user_id, new_epoch)
            if not ok:
                await update.effective_message.reply_text("Не удалось обновить.")
                return
            # перепланировать jobs
            _cancel_reminder_jobs(context.application, item_id)
            updated = db.get_reminder(item_id, user_id)
            if updated is not None:
                _schedule_reminder(context.application, updated)
                await update.effective_message.reply_text(
                    f"✅ Напоминание #{item_id} перенесено на {fmt.fmt_fire_at(new_epoch, cfg.tz)}."
                )
        return


async def _parse_new_fire_at(
    context: ContextTypes.DEFAULT_TYPE,
    raw: str,
    tz_name: str,
) -> int | None:
    """Сначала пытаемся ISO-парсер, если не вышло — отправляем строку в LLM ReminderSpec."""
    epoch = parse_iso_to_epoch(raw, tz_name)
    if epoch is not None:
        return epoch
    llm: GroqLLM = context.bot_data["llm"]
    try:
        summary = await llm.structure(
            f"Создай напоминание: {raw}",
            now_context=now_context_block(tz_name),
        )
    except Exception:  # noqa: BLE001
        logger.exception("LLM failed to parse new fire_at")
        return None
    if not summary.reminders:
        return None
    return parse_iso_to_epoch(summary.reminders[0].fire_at_iso, tz_name)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None:
        return
    user_id = update.effective_user.id
    if not _is_allowed(context, user_id):
        await q.answer("Доступ запрещён.", show_alert=True)
        return
    db: Database = context.bot_data["db"]
    cfg: Config = context.bot_data["cfg"]
    data = q.data

    if data == "nop":
        await q.answer("Отмена.")
        try:
            await q.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        return

    parts = data.split(":")
    if len(parts) < 2:
        await q.answer("Неизвестное действие.")
        return
    # n:e:title:42 / n:e:cat:42 / n:e:tags:42
    if parts[0] == "n" and parts[1] == "e" and len(parts) == 4:
        await q.answer()
        await _ask_for_edit(context, q.message.chat_id, "note", int(parts[3]), parts[2])
        return
    # n:open:42
    if parts[0] == "n" and parts[1] == "open" and len(parts) == 3:
        note_id = int(parts[2])
        note = db.get_note(note_id, user_id)
        if note is None:
            await q.answer("Заметка не найдена.", show_alert=True)
            return
        await q.answer()
        await _reply_long_html(
            q.message,
            fmt.format_note(note, tz_name=cfg.tz),
            reply_markup=kb.note_actions_kb(note.note_id),
        )
        return
    # n:del:42 → подтверждение
    if parts[0] == "n" and parts[1] == "del" and len(parts) == 3:
        await q.answer()
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=f"🗑 Удалить заметку #{parts[2]}?",
            reply_markup=kb.confirm_delete_note_kb(int(parts[2])),
        )
        return
    # n:polish:42 — полировка транскрипта LLM (заменяет заметку полированным текстом)
    if parts[0] == "n" and parts[1] == "polish" and len(parts) == 3:
        note_id = int(parts[2])
        note = db.get_note(note_id, user_id)
        if note is None:
            await q.answer("Заметка не найдена.", show_alert=True)
            return
        await q.answer("Полирую…")
        llm: GroqLLM = context.bot_data["llm"]
        try:
            polished = await llm.polish(note.transcript)
        except LLMError as e:
            await q.message.reply_text(f"⚠️ Не смог полировать: {e}")
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("polish failed")
            await q.message.reply_text(f"⚠️ Ошибка полировки ({type(e).__name__}).")
            return
        # удаляем оригинальную заметку из БД и заменяем сообщение полированным текстом.
        db.delete_note(note_id, user_id)
        header = f"<b>📝 Полированный текст</b>\n\n"
        body = fmt.esc(polished)
        text = header + body
        try:
            if len(text) <= 3900:
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            else:
                # длинный текст не влезает в одно сообщение → удаляем оригинал и шлём новый(е)
                try:
                    await q.message.delete()
                except Exception:  # noqa: BLE001
                    pass
                await _reply_long_html(q.message, text)
        except Exception:  # noqa: BLE001
            # edit может упасть, если сообщение слишком старое или содержит документ —
            # тогда удалим оригинал и пришлём новое.
            try:
                await q.message.delete()
            except Exception:  # noqa: BLE001
                pass
            await _reply_long_html(q.message, text)
        return
    # n:del_yes:42
    if parts[0] == "n" and parts[1] == "del_yes" and len(parts) == 3:
        note_id = int(parts[2])
        ok = db.delete_note(note_id, user_id)
        await q.answer("Удалено." if ok else "Не найдено.")
        try:
            await q.edit_message_text(
                f"🗑 Заметка #{note_id} удалена." if ok else f"Заметка #{note_id} не найдена.",
            )
        except Exception:  # noqa: BLE001
            pass
        return
    # r:e:time:5 / r:e:text:5
    if parts[0] == "r" and parts[1] == "e" and len(parts) == 4:
        await q.answer()
        field_map = {"time": "rtime", "text": "rtext"}
        field = field_map.get(parts[2], parts[2])
        await _ask_for_edit(context, q.message.chat_id, "rem", int(parts[3]), field)
        return
    # r:cancel:5 → подтверждение
    if parts[0] == "r" and parts[1] == "cancel" and len(parts) == 3:
        await q.answer()
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=f"🗑 Отменить напоминание #{parts[2]}?",
            reply_markup=kb.confirm_cancel_reminder_kb(int(parts[2])),
        )
        return
    # r:cancel_yes:5
    if parts[0] == "r" and parts[1] == "cancel_yes" and len(parts) == 3:
        rid = int(parts[2])
        ok = db.cancel_reminder(rid, user_id)
        if ok:
            _cancel_reminder_jobs(context.application, rid)
        await q.answer("Отменено." if ok else "Не найдено.")
        try:
            await q.edit_message_text(
                f"🗑 Напоминание #{rid} отменено." if ok else f"Напоминание #{rid} не найдено или уже отменено.",
            )
        except Exception:  # noqa: BLE001
            pass
        return

    await q.answer("Неизвестное действие.")


# ---- main voice handler -----------------------------------------------


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_allowed(context, user.id):
        await _deny(update)
        return

    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    stt: GroqSTT = context.bot_data["stt"]
    llm: GroqLLM = context.bot_data["llm"]

    db.upsert_user(user.id, user.username, user.first_name, user.last_name)

    msg = update.effective_message
    tg_audio = msg.voice or msg.audio
    if tg_audio is None:
        return

    size_mb = (tg_audio.file_size or 0) / (1024 * 1024)
    if size_mb > cfg.max_audio_mb:
        await msg.reply_text(
            f"⚠️ Файл {size_mb:.1f} МБ больше лимита {cfg.max_audio_mb} МБ. "
            "Разбейте аудио или пришлите короче."
        )
        return

    placeholder = await msg.reply_text(fmt.format_processing())
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tg_file = await tg_audio.get_file()
        src = tmp_path / f"in_{tg_audio.file_unique_id}{Path(tg_file.file_path or 'audio').suffix or '.ogg'}"
        wav = tmp_path / "audio.wav"
        await tg_file.download_to_drive(src)
        try:
            _convert_to_wav(src, wav)
            upload_path = wav
        except Exception as e:
            logger.warning("ffmpeg failed: %s; uploading source file as-is", e)
            upload_path = src

        # --- STT -------------------------------------------------------
        lang_pref = (db.get_lang(user.id) or cfg.default_lang or "auto").lower()
        try:
            t0 = time.time()
            stt_out = await stt.transcribe(upload_path, lang=lang_pref)
            stt_seconds = time.time() - t0
        except STTError as e:
            await placeholder.edit_text(f"❌ Ошибка распознавания: {e}")
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("STT error")
            await placeholder.edit_text(f"❌ Не удалось распознать речь ({type(e).__name__}).")
            return

        transcript = stt_out["text"]
        lang = stt_out.get("language")
        duration = stt_out.get("duration") or float(tg_audio.duration or 0)

        # --- сохранить аудио, если просили --------------------------
        audio_path_saved: str | None = None
        if cfg.keep_audio:
            cfg.audio_dir.mkdir(parents=True, exist_ok=True)
            saved = cfg.audio_dir / f"{user.id}_{int(time.time())}{src.suffix}"
            saved.write_bytes(src.read_bytes())
            audio_path_saved = str(saved)

    # Если ждём ответ на edit-запрос — голос трактуется как ответ.
    pending = context.user_data.get("pending_edit") if context.user_data is not None else None
    if pending and transcript.strip():
        try:
            await placeholder.delete()
        except Exception:  # noqa: BLE001
            pass
        await _apply_pending_edit(update, context, pending, transcript)
        return

    # --- LLM + save + reminders ---------------------------------------
    await _process_summary(
        update,
        context,
        placeholder=placeholder,
        transcript=transcript,
        lang=lang,
        duration=duration,
        source="voice" if msg.voice else "audio",
        audio_path_saved=audio_path_saved,
    )
    logger.info("note for user %d: %.1fs audio, STT %.1fs", user.id, duration, stt_seconds)


# ---- shared LLM + save + reminders pipeline ----------------------------


async def _process_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    placeholder,
    transcript: str,
    lang: str | None,
    duration: float | None,
    source: str,  # "voice" | "audio" | "text"
    audio_path_saved: str | None,
) -> None:
    """Общий пайплайн для голоса и текста: LLM → DB → планирование напоминаний → ответ."""
    user = update.effective_user
    cfg: Config = context.bot_data["cfg"]
    db: Database = context.bot_data["db"]
    llm: GroqLLM = context.bot_data["llm"]
    msg = update.effective_message

    try:
        summary: Summary = await llm.structure(
            transcript, now_context=now_context_block(cfg.tz)
        )
    except LLMError as e:
        await placeholder.edit_text(
            f"⚠️ Не смог структурировать: {e}\n\n📄 Текст:\n{transcript[:3500]}",
            parse_mode=None,
            disable_web_page_preview=True,
        )
        return
    except Exception as e:  # noqa: BLE001
        logger.exception("LLM error")
        await placeholder.edit_text(
            f"⚠️ Ошибка структурирования ({type(e).__name__}).\n\n📄 Текст:\n{transcript[:3500]}",
            parse_mode=None,
            disable_web_page_preview=True,
        )
        return

    note_id = db.add_note(
        user_id=user.id,
        transcript=transcript,
        summary=summary.model_dump(),
        title=summary.title,
        lang=lang,
        duration_seconds=duration,
        audio_path=audio_path_saved,
        source=source,
    )
    note = db.get_note(note_id, user.id)
    assert note is not None

    # materialize + schedule reminders
    scheduled = materialize_reminders(
        db,
        user.id,
        summary.reminders,
        cfg.tz,
        cfg.default_advance_minutes,
        source_note_id=note_id,
    )
    for r in scheduled:
        _schedule_reminder(context.application, r)

    # Reminder-only режим: пользователь сказал «напомни в X встретиться с Y» —
    # развёрнутое саммари не нужно, шлём только короткое подтверждение
    # с inline-кнопками. Заметка всё равно сохраняется в БД (доступна через /list,
    # /search, /get_N) — просто не выводим её визуально.
    if summary.is_reminder_only and scheduled:
        await placeholder.delete()
        for r in scheduled:
            advance_part = (
                f" (с уведомлением за {r.advance_minutes} мин)"
                if r.advance_minutes > 0 else ""
            )
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=(
                    f"✅ Напоминание #{r.reminder_id} создано.\n"
                    f"⏰ <b>{fmt.fmt_fire_at(r.fire_at, cfg.tz)}</b>{advance_part}\n"
                    f"📝 {fmt.esc(r.text)}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb.reminder_actions_kb(r.reminder_id),
            )
        logger.info(
            "reminder-only note #%d for user %d: source=%s, reminders=%d",
            note_id, user.id, source, len(scheduled),
        )
        return

    text = fmt.format_note(note, tz_name=cfg.tz, scheduled_reminders=scheduled)
    chunks = _split_for_telegram(text, limit=3900)
    safe = not _has_unsafe_chunk(chunks, 3900)
    note_kb = kb.note_actions_kb(note.note_id)
    if safe:
        # placeholder редактируем без клавиатуры (кнопки вешаем на последнее сообщение,
        # чтобы было одно «активное» меню под полным текстом).
        await placeholder.edit_text(
            chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=note_kb if len(chunks) == 1 else None,
        )
        for i, chunk in enumerate(chunks[1:], start=1):
            kw = {"parse_mode": ParseMode.HTML, "disable_web_page_preview": True}
            if i == len(chunks) - 1:
                kw["reply_markup"] = note_kb
            await context.bot.send_message(chat_id=msg.chat_id, text=chunk, **kw)
    else:
        pieces = [text[i:i + 3900] for i in range(0, len(text), 3900)]
        await placeholder.edit_text(
            pieces[0], parse_mode=None, disable_web_page_preview=True,
            reply_markup=note_kb if len(pieces) == 1 else None,
        )
        for i, piece in enumerate(pieces[1:], start=1):
            kw = {"parse_mode": None, "disable_web_page_preview": True}
            if i == len(pieces) - 1:
                kw["reply_markup"] = note_kb
            await context.bot.send_message(chat_id=msg.chat_id, text=piece, **kw)

    # Отдельные сообщения с кнопками для каждого созданного напоминания —
    # чтобы их можно было сразу перенести/отменить, не открывая /reminders.
    for r in scheduled:
        await context.bot.send_message(
            chat_id=msg.chat_id,
            text=f"⏰ Напоминание #{r.reminder_id}: {fmt.esc(r.text)}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb.reminder_actions_kb(r.reminder_id),
        )
    logger.info(
        "note #%d for user %d: source=%s, reminders=%d",
        note_id, user.id, source, len(scheduled),
    )


# ---- reminders scheduling ---------------------------------------------


async def _job_reminder_advance(context: ContextTypes.DEFAULT_TYPE) -> None:
    rid = context.job.data["rid"]
    db: Database = context.application.bot_data["db"]
    cfg: Config = context.application.bot_data["cfg"]
    r = db.get_reminder_any(rid)
    if r is None or r.status != "pending":
        return
    try:
        await context.bot.send_message(
            chat_id=r.user_id,
            text=fmt.format_reminder_advance(r, cfg.tz),
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # noqa: BLE001
        logger.exception("failed to send advance reminder %d", rid)


async def _job_reminder_fire(context: ContextTypes.DEFAULT_TYPE) -> None:
    rid = context.job.data["rid"]
    db: Database = context.application.bot_data["db"]
    cfg: Config = context.application.bot_data["cfg"]
    r = db.get_reminder_any(rid)
    if r is None or r.status != "pending":
        return
    try:
        await context.bot.send_message(
            chat_id=r.user_id,
            text=fmt.format_reminder_fire(r, cfg.tz),
            parse_mode=ParseMode.HTML,
        )
    except Exception:  # noqa: BLE001
        # Если уведомление не доставилось — оставляем reminder в pending, чтобы
        # JobQueue/restart-rescheduler смог его попробовать ещё раз. Не помечаем fired.
        logger.exception("failed to send fire reminder %d; leaving as pending", rid)
        return
    db.mark_reminder_fired(rid)


async def _job_prune_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет fired/cancelled напоминания старше 24 часов."""
    db: Database = context.application.bot_data["db"]
    cutoff = int(time.time()) - 24 * 3600
    n = db.prune_old_reminders(cutoff)
    if n:
        logger.info("pruned %d old reminders", n)


def _schedule_reminder(app: Application, reminder: Reminder) -> None:
    jq = app.job_queue
    if jq is None:
        logger.warning("job_queue недоступен; напоминание #%d не запланировано", reminder.reminder_id)
        return
    now = int(time.time())
    advance_at = reminder.fire_at - reminder.advance_minutes * 60
    if reminder.advance_minutes > 0 and advance_at > now:
        jq.run_once(
            _job_reminder_advance,
            when=advance_at - now,
            data={"rid": reminder.reminder_id},
            name=f"reminder:advance:{reminder.reminder_id}",
        )
    # Всегда планируем fire-job, даже если fire_delay <= 0 (рейс-кондишн между
    # past-check у вызывающего и re-capture'ом now здесь). JobQueue корректно
    # выполнит задачу немедленно при when=0, а callback пометит reminder fired.
    fire_delay = reminder.fire_at - now
    jq.run_once(
        _job_reminder_fire,
        when=max(fire_delay, 0),
        data={"rid": reminder.reminder_id},
        name=f"reminder:fire:{reminder.reminder_id}",
    )


def _cancel_reminder_jobs(app: Application, reminder_id: int) -> None:
    jq = app.job_queue
    if jq is None:
        return
    for name in (f"reminder:advance:{reminder_id}", f"reminder:fire:{reminder_id}"):
        for job in jq.get_jobs_by_name(name):
            job.schedule_removal()


async def _on_post_init(app: Application) -> None:
    """Пере-планируем все pending напоминания, регистрируем периодическую чистку,
    и регистрируем выпадающее меню команд (setMyCommands).

    Для напоминаний с fire_at <= now (например, оставшихся в pending после неудачной
    доставки в прошлом или после простоя бота) пытаемся отправить запоздалое уведомление
    с пометкой «пропущено». Помечаем fired только при успехе — иначе остаётся pending,
    чтобы следующий рестарт попробовал ещё раз. Это сохраняет retry-семантику
    _job_reminder_fire.
    """
    db: Database = app.bot_data["db"]
    cfg: Config = app.bot_data["cfg"]
    pending = db.all_pending_reminders()
    now = int(time.time())
    rescheduled = 0
    missed_sent = 0
    missed_kept = 0
    for r in pending:
        if r.fire_at <= now:
            try:
                await app.bot.send_message(
                    chat_id=r.user_id,
                    text="⚠️ Пропущенное напоминание (не доставилось ранее):\n\n"
                    + fmt.format_reminder_fire(r, cfg.tz),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "failed to send belated reminder %d; leaving as pending for next restart",
                    r.reminder_id,
                )
                missed_kept += 1
                continue
            db.mark_reminder_fired(r.reminder_id)
            missed_sent += 1
            continue
        _schedule_reminder(app, r)
        rescheduled += 1
    logger.info(
        "rescheduled %d pending reminders, sent %d belated, %d kept pending",
        rescheduled, missed_sent, missed_kept,
    )
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _job_prune_reminders,
            interval=24 * 3600,
            first=60,
            name="prune-reminders",
        )
    try:
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, desc in BOT_COMMANDS]
        )
        logger.info("set %d bot commands", len(BOT_COMMANDS))
    except Exception:  # noqa: BLE001
        logger.exception("failed to set bot commands")


# ---- bootstrap ---------------------------------------------------------


def build_app() -> Application:
    cfg = Config.from_env()
    db = Database(cfg.db_path)
    stt = GroqSTT(cfg.groq_api_key, cfg.stt_model)
    llm = GroqLLM(cfg.groq_api_key, cfg.llm_model)

    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .defaults(defaults)
        .post_init(_on_post_init)
        .build()
    )
    app.bot_data["cfg"] = cfg
    app.bot_data["db"] = db
    app.bot_data["stt"] = stt
    app.bot_data["llm"] = llm

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("lang", cmd_lang))
    app.add_handler(CommandHandler("forget_all", cmd_forget_all))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("export_ical", cmd_export_ical))
    app.add_handler(CommandHandler("txt", cmd_txt))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # подхватываем динамические /get_N, /delete_N — telegram считает их командами
    app.add_handler(MessageHandler(filters.COMMAND, on_text))

    app.add_error_handler(_error)
    return app


# Список команд для setMyCommands (выпадающее меню «Меню» рядом со скрепкой).
BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "🚀 Приветствие и краткая справка"),
    ("help", "❓ Полный список команд"),
    ("last", "📄 Последняя заметка"),
    ("list", "📚 Список последних заметок"),
    ("search", "🔍 Поиск по заметкам"),
    ("txt", "📝 Полировать последнее голосовое"),
    ("reminders", "⏰ Активные напоминания"),
    ("export_ical", "📅 Экспорт напоминаний в .ics"),
    ("stats", "📊 Статистика"),
    ("lang", "🌐 Язык распознавания (ru/en/auto)"),
    ("forget_all", "🗑 Удалить все заметки"),
]


async def _error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled update error", exc_info=context.error)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    app = build_app()
    cfg: Config = app.bot_data["cfg"]
    logger.info(
        "Secretary-bot запущен. STT=%s, LLM=%s, разрешённых пользователей: %d",
        cfg.stt_model, cfg.llm_model, len(cfg.allowed_user_ids),
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
