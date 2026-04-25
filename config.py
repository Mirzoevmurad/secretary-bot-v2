"""Конфигурация secretary-bot из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    groq_api_key: str
    allowed_user_ids: frozenset[int]
    db_path: Path
    stt_model: str
    llm_model: str
    keep_audio: bool
    default_lang: str  # "ru" | "en" | "auto"
    max_audio_mb: int
    audio_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("SECRETARY_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("SECRETARY_BOT_TOKEN не задан")
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            raise RuntimeError("GROQ_API_KEY не задан")

        raw_ids = os.getenv("ALLOWED_USER_IDS", "").strip()
        if not raw_ids:
            # fallback: TELEGRAM_OWNER_ID — одиночный владелец
            owner = os.getenv("TELEGRAM_OWNER_ID", "").strip()
            raw_ids = owner
        ids: set[int] = set()
        for chunk in raw_ids.split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    ids.add(int(chunk))
                except ValueError as exc:
                    raise RuntimeError(f"ALLOWED_USER_IDS: невалидный id {chunk!r}") from exc
        if not ids:
            raise RuntimeError("ALLOWED_USER_IDS (или TELEGRAM_OWNER_ID) должен содержать хотя бы один id")

        db_path = Path(os.getenv("DB_PATH", "data/secretary.sqlite"))
        audio_dir = Path(os.getenv("AUDIO_DIR", "data/audio"))
        return cls(
            bot_token=token,
            groq_api_key=groq_key,
            allowed_user_ids=frozenset(ids),
            db_path=db_path,
            stt_model=os.getenv("STT_MODEL", "whisper-large-v3-turbo"),
            llm_model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
            keep_audio=_bool(os.getenv("KEEP_AUDIO", "false")),
            default_lang=os.getenv("DEFAULT_LANG", "auto").lower(),
            max_audio_mb=int(os.getenv("MAX_AUDIO_MB", "25")),
            audio_dir=audio_dir,
        )


def _bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "yes", "on", "y"}
