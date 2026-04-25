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
from db import Database, Note
from llm import GroqLLM, LLMError, Summary
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
    db: Database = context.bot_data["db"]
    note = db.last_note(update.effective_user.id)
    if note is None:
        await update.effective_message.reply_text("Заметок пока нет.")
        return
    await update.effective_message.reply_html(fmt.format_note(note))


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


# --- /get_N и /delete_N как динамические команды -----------------------


_GET_RE = re.compile(r"^/get_(\d+)(?:@\w+)?$")
_DELETE_RE = re.compile(r"^/delete_(\d+)(?:@\w+)?$")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(context, update.effective_user.id):
        await _deny(update)
        return
    txt = (update.effective_message.text or "").strip()
    m = _GET_RE.match(txt)
    if m:
        note_id = int(m.group(1))
        db: Database = context.bot_data["db"]
        note = db.get_note(note_id, update.effective_user.id)
        if note is None:
            await update.effective_message.reply_text(f"Заметка #{note_id} не найдена.")
            return
        await update.effective_message.reply_html(fmt.format_transcript(note))
        return
    m = _DELETE_RE.match(txt)
    if m:
        note_id = int(m.group(1))
        db: Database = context.bot_data["db"]
        ok = db.delete_note(note_id, update.effective_user.id)
        await update.effective_message.reply_text(
            f"🗑 Заметка #{note_id} удалена." if ok else f"Заметка #{note_id} не найдена."
        )
        return
    # любой другой текст — подсказка
    await update.effective_message.reply_text(
        "Пришлите голосовое сообщение 🎤 — я обработаю. /help для команд."
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

        # --- LLM -------------------------------------------------------
        try:
            summary: Summary = await llm.structure(transcript)
        except LLMError as e:
            await placeholder.edit_text(
                f"⚠️ Распознал текст, но не смог структурировать: {e}\n\n"
                f"📄 Транскрипт:\n{transcript[:3500]}"
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("LLM error")
            await placeholder.edit_text(
                f"⚠️ Ошибка структурирования ({type(e).__name__}).\n\n"
                f"📄 Транскрипт:\n{transcript[:3500]}"
            )
            return

        # --- сохранить ------------------------------------------------
        audio_path_saved: str | None = None
        if cfg.keep_audio:
            cfg.audio_dir.mkdir(parents=True, exist_ok=True)
            saved = cfg.audio_dir / f"{user.id}_{int(time.time())}{src.suffix}"
            saved.write_bytes(src.read_bytes())
            audio_path_saved = str(saved)

        note_id = db.add_note(
            user_id=user.id,
            transcript=transcript,
            summary=summary.model_dump(),
            title=summary.title,
            lang=lang,
            duration_seconds=duration,
            audio_path=audio_path_saved,
            source="voice" if msg.voice else "audio",
        )
        note = db.get_note(note_id, user.id)
        assert note is not None

    await placeholder.edit_text(
        fmt.format_note(note),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    logger.info(
        "note #%d for user %d: %.1fs audio, STT %.1fs",
        note_id, user.id, duration, stt_seconds,
    )


# ---- bootstrap ---------------------------------------------------------


def build_app() -> Application:
    cfg = Config.from_env()
    db = Database(cfg.db_path)
    stt = GroqSTT(cfg.groq_api_key, cfg.stt_model)
    llm = GroqLLM(cfg.groq_api_key, cfg.llm_model)

    defaults = Defaults(parse_mode=ParseMode.HTML)
    app = ApplicationBuilder().token(cfg.bot_token).defaults(defaults).build()
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
