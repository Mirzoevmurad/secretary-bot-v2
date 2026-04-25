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
        sentences = c.replace(". ", ".\x00").split("\x00")
        sub = _group(sentences, "")
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
    # fallback: plain text
    plain = text  # без перевода в plain — Telegram сам отрисует тэги как символы
    for i in range(0, len(plain), limit):
        await msg.reply_text(plain[i:i + limit], disable_web_page_preview=True)


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
    await _reply_long_html(update.effective_message, fmt.format_note(note))


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
        await _reply_long_html(update.effective_message, fmt.format_transcript(note))
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

    text = fmt.format_note(note)
    # Telegram message limit = 4096 символов. Если влезает — редактируем placeholder; иначе редактируем
    # placeholder первым куском и шлём остальные доп. сообщениями.
    chunks = _split_for_telegram(text, limit=3900)
    safe = not _has_unsafe_chunk(chunks, 3900)
    if safe:
        await placeholder.edit_text(
            chunks[0],
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        for chunk in chunks[1:]:
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    else:
        # очень редкий путь: атомарный кусок без границ — режем посимвольно как plain text
        await placeholder.edit_text(text[:3900], disable_web_page_preview=True)
        for i in range(3900, len(text), 3900):
            await context.bot.send_message(
                chat_id=msg.chat_id,
                text=text[i:i + 3900],
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
