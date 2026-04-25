"""Speech-to-text через Groq Whisper."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from groq import Groq


logger = logging.getLogger(__name__)


class STTError(Exception):
    pass


class GroqSTT:
    def __init__(self, api_key: str, model: str = "whisper-large-v3-turbo") -> None:
        self._client = Groq(api_key=api_key)
        self._model = model

    async def transcribe(self, audio_path: Path, lang: str = "auto") -> dict:
        """Возвращает {text, language, duration}."""
        return await asyncio.to_thread(self._transcribe_sync, audio_path, lang)

    def _transcribe_sync(self, audio_path: Path, lang: str) -> dict:
        with audio_path.open("rb") as f:
            kwargs = {
                "file": (audio_path.name, f.read()),
                "model": self._model,
                "response_format": "verbose_json",
                "temperature": 0.0,
            }
            if lang and lang != "auto":
                kwargs["language"] = lang
            resp = self._client.audio.transcriptions.create(**kwargs)

        # groq возвращает pydantic-объект; обращаемся как dict-подобно
        data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
        text = (data.get("text") or "").strip()
        if not text:
            raise STTError("Whisper вернул пустой текст")
        return {
            "text": text,
            "language": data.get("language"),
            "duration": data.get("duration"),  # seconds, float
        }
