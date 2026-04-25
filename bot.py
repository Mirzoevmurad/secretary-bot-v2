"""Secretary-bot: Telegram голосовой бот-секретарь поверх Groq Whisper + LLM."""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
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
    reminders_to_ical,
)
from stt import GroqSTT, STTError
import formatter as fmt


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


async def _reply_long_html(msg, text: str, limit: int = 3900) -> None:
    """Отправляет длинный HTML-текст одним или несколькими сообщениями.

    Если все куски в безопасных границах — шлём с parse_mode=HTML.
    Если нашёлся атомарный кусок > limit (крайне маловероятно) — режем посимвольно
    и шлём как plain text, чтобы Telegram не отверг разорванный тэг.
    """
    chunks = _split_for_telegram(text, limit=limit)
    safe = not _has_unsafe_chunk(chunks, limit)
    if safe:
        for chunk in chunks:
            await msg.reply_html(chunk, disable_web_page_preview=True)
        return
    # fallback: режем посимвольно и шлём как plain text. parse_mode=None обязателен —
    # Application имеет Defaults(parse_mode=ParseMode.HTML), без явного None Telegram
    # будет интерпретировать порванный тэг как HTML и отвергнет сообщение.
    for i in range(0, len(text), limit):
        await msg.reply_text(text[i:i + limit], parse_mode=None, disable_web_page_preview=True)


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
    await _reply_long_html(update.effective_message, fmt.format_note(note, tz_name=cfg.tz))


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    limit = 10
    args = (context.args or [])
    if args and args[0].isdigit():
        limit = min(int(args[0]), 50)
    db: Database = context.bot_data["db"]
    notes = db.list_notes(update.effective_user.id, limit=limit)
    await update.effective_message.reply_html(fmt.format_list(notes))


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    q = " ".join(context.args or []).strip()
    if not q:
        await update.effective_message.reply_text("Использование: /search <запрос>")
        return
    db: Database = context.bot_data["db"]
    notes = db.search(update.effective_user.id, q, limit=10)
    await update.effective_message.reply_html(fmt.format_search(notes, q))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    db: Database = context.bot_data["db"]
    s = db.stats(update.effective_user.id)
    await update.effective_message.reply_html(fmt.format_stats(s))


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
    await _reply_long_html(
        update.effective_message, fmt.format_reminders_list(items, cfg.tz)
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

    m = _GET_RE.match(txt)
    if m:
        note_id = int(m.group(1))
        note = db.get_note(note_id, update.effective_user.id)
        if note is None:
            await update.effective_message.reply_text(f"Заметка #{note_id} не найдена.")
            return
        await _reply_long_html(update.effective_message, fmt.format_transcript(note))
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

    text = fmt.format_note(note, tz_name=cfg.tz, scheduled_reminders=scheduled)
    chunks = _split_for_telegram(text, limit=3900)
    safe = not _has_unsafe_chunk(chunks, 3900)
    if safe:
        await placeholder.edit_text(
            chunks[0], parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        for chunk in chunks[1:]:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    else:
        await placeholder.edit_text(
            text[:3900], parse_mode=None, disable_web_page_preview=True
        )
        for i in range(3900, len(text), 3900):
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=text[i:i + 3900],
                parse_mode=None,
                disable_web_page_preview=True,
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
    """Пере-планируем все pending напоминания и регистрируем периодическую чистку."""
    db: Database = app.bot_data["db"]
    pending = db.all_pending_reminders()
    now = int(time.time())
    rescheduled = 0
    for r in pending:
        if r.fire_at <= now:
            db.mark_reminder_fired(r.reminder_id)
            continue
        _schedule_reminder(app, r)
        rescheduled += 1
    logger.info("rescheduled %d pending reminders", rescheduled)
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _job_prune_reminders,
            interval=24 * 3600,
            first=60,
            name="prune-reminders",
        )


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

    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # подхватываем динамические /get_N, /delete_N — telegram считает их командами
    app.add_handler(MessageHandler(filters.COMMAND, on_text))

    app.add_error_handler(_error)
    return app


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
