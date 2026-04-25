"""Структурирование транскрипта через Groq Llama."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from groq import Groq
from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — внимательный персональный ассистент-секретарь. На вход получаешь транскрипт голосовой заметки на русском или английском языке. Твоя задача — превратить устную, часто неструктурированную речь в развёрнутый, информативный конспект, по которому через неделю можно полностью восстановить ход мысли автора.

Строго верни JSON (без лишнего текста) ровно в такой форме:
{
  "title": "содержательный заголовок (5–10 слов) на том же языке, что и транскрипт",
  "tldr": "одно предложение на 15–30 слов: главная мысль / суть заметки",
  "details": "развёрнутый абзац (4–8 предложений) — пересказ заметки в связном виде, с контекстом, причинами, упомянутыми деталями и нюансами. НЕ переписывай транскрипт дословно, но сохрани все смысловые детали (имена, числа, даты, аргументы). Текст должен читаться как связный конспект, а не как буллеты.",
  "summary": ["развёрнутый пункт 1", "развёрнутый пункт 2", "..."],
  "tasks": [
    {"what": "конкретная задача с контекстом — не просто \"сделать X\", а одно-два предложения с деталями: зачем, какие требования", "who": "кто делает / null если не указано", "when": "срок / null если не указан"}
  ],
  "open_questions": ["развёрнутый вопрос 1 — формулируй полным предложением, чтобы был понятен контекст", "..."],
  "decisions": ["явное принятое решение, с обоснованием если оно прозвучало"],
  "tags": ["тег1", "тег2", "..."],
  "category": "одна из: Работа | Личное | Идея | Встреча | Покупки | Учёба | Здоровье | Финансы | Другое"
}

Жёсткие правила:
- НЕ сокращай содержание. Если в речи есть деталь — она должна попасть либо в details, либо в summary, либо в task. Лучше пять подробных пунктов, чем десять обрывочных слов.
- summary: 4–10 пунктов, КАЖДЫЙ пункт — это одно полноценное предложение (10–25 слов), а не двух-словное название. Каждый пункт раскрывает отдельный смысловой блок: что обсуждалось, какие аргументы, какие данные, какие выводы. Запрещены пункты вида "Тестирование моделей" или "Хранение данных" — пиши "Обсудили необходимость тестирования моделей перед раскаткой, потому что …".
- tasks: каждое поле "what" — полноценное действие с контекстом и параметрами, минимум 6–15 слов. Запрещены односложные формулировки. Если в речи задача не сформулирована явно как "надо сделать X" — НЕ выдумывай её, лучше отрази в open_questions или decisions.
- open_questions: вопросы, на которые в записи ответа нет, но автор сам их озвучил или они логически висят. Полные предложения.
- decisions: только то, что автор явно решил/выбрал (например "решил использовать Postgres"). Пустой массив, если решений не было.
- title: содержательный, отражает суть, не общий ("Заметка про X" — плохо; "Архитектура аналитического Telegram-бота на Postgres" — хорошо).
- tldr: одно предложение, главное "о чём это вообще".
- details: связный текст, без буллетов, с предлогами и связками. Пиши так, как написал бы заметку человек, который пересказывает запись коллеге.
- НЕ выдумывай факты, которых в транскрипте нет. Но раскрой и переформулируй то, что есть.
- Если в транскрипте 2–3 фразы и нечего расписывать — все равно дай хотя бы 2–3 пункта summary, постарайся сохранить контекст.
- Если в транскрипте нет задач / вопросов / решений — возвращай пустой массив, но ключ обязательно.
- Для tasks: если исполнитель/дедлайн не указаны — ставь null (не строку "не указано").
- Отвечай только валидным JSON, без markdown-оборачивания, без префикса/суффикса."""


class Task(BaseModel):
    what: str
    who: str | None = None
    when: str | None = None


class Summary(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    tldr: str = ""
    details: str = ""
    summary: list[str] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
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
            max_tokens=4000,
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
                "title": str(raw.get("title", "Без заголовка"))[:200] or "Без заголовка",
                "tldr": str(raw.get("tldr", ""))[:500],
                "details": str(raw.get("details", "")),
                "summary": [str(x) for x in raw.get("summary", []) if isinstance(x, (str, int, float))],
                "tasks": [],
                "open_questions": [str(x) for x in raw.get("open_questions", []) if isinstance(x, str)],
                "decisions": [str(x) for x in raw.get("decisions", []) if isinstance(x, str)],
                "tags": [str(x) for x in raw.get("tags", []) if isinstance(x, str)],
                "category": str(raw.get("category", "Другое")),
            }
            return Summary.model_validate(fallback)
