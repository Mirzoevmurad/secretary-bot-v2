"""Структурирование транскрипта через Groq Llama."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from groq import Groq
from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — ассистент-секретарь. На вход получаешь транскрипт голосовой заметки на русском или английском языке. Нужно извлечь структуру.

Строго верни JSON (без лишнего текста) ровно в такой форме:
{
  "title": "короткий заголовок (3-7 слов) на том же языке, что и транскрипт",
  "summary": ["пункт 1", "пункт 2", "..."],
  "tasks": [
    {"what": "что сделать", "who": "кто / null", "when": "когда / null"}
  ],
  "open_questions": ["вопрос 1", "..."],
  "tags": ["тег1", "тег2"],
  "category": "одна из: Работа | Личное | Идея | Встреча | Покупки | Учёба | Здоровье | Финансы | Другое"
}

Правила:
- Если в транскрипте нет задач / вопросов / явных тегов — возвращай пустой массив, но ключ обязательно.
- Не выдумывай детали, которых нет в транскрипте.
- Для tasks: если исполнитель/дедлайн не указаны — ставь null (не строку "не указано").
- summary: 2–7 пунктов, каждый — одна короткая фраза, без "."-мусора.
- title: без кавычек, без точки в конце.
- Отвечай только валидным JSON, без markdown-оборачивания."""


class Task(BaseModel):
    what: str
    who: str | None = None
    when: str | None = None


class Summary(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str = "Другое"


class LLMError(Exception):
    pass


class GroqLLM:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._client = Groq(api_key=api_key)
        self._model = model

    async def structure(self, transcript: str) -> Summary:
        if not transcript.strip():
            raise LLMError("Пустой транскрипт")
        return await asyncio.to_thread(self._structure_sync, transcript)

    def _structure_sync(self, transcript: str) -> Summary:
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        content = completion.choices[0].message.content or "{}"
        try:
            raw: Any = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("LLM returned non-JSON: %r", content[:200])
            raise LLMError(f"LLM вернул невалидный JSON: {exc}") from exc
        try:
            return Summary.model_validate(raw)
        except ValidationError as exc:
            logger.warning("LLM JSON не прошёл валидацию: %s", exc)
            # мягкая деградация: возвращаем минимум из того, что есть
            fallback = {
                "title": str(raw.get("title", "Без заголовка"))[:120] or "Без заголовка",
                "summary": [str(x) for x in raw.get("summary", []) if isinstance(x, (str, int, float))],
                "tasks": [],
                "open_questions": [str(x) for x in raw.get("open_questions", []) if isinstance(x, str)],
                "tags": [str(x) for x in raw.get("tags", []) if isinstance(x, str)],
                "category": str(raw.get("category", "Другое")),
            }
            return Summary.model_validate(fallback)
