"""Структурирование транскрипта через Groq Llama."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from groq import Groq
from pydantic import BaseModel, Field, ValidationError


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Ты — внимательный персональный ассистент-секретарь. На вход получаешь транскрипт голосовой заметки или текстовое сообщение на русском или английском. Твоя задача — извлечь напоминания, классифицировать намерение автора и (если нужно) сделать развёрнутый конспект.

Строго верни JSON (без лишнего текста) ровно в такой форме:
{
  "title": "содержательный заголовок (5–10 слов) на том же языке, что и транскрипт",
  "details": "один связный развёрнутый абзац (4–10 предложений) — пересказ заметки с контекстом, причинами, упомянутыми деталями. Сохраняет все смысловые детали (имена, числа, даты, аргументы). Это ЕДИНСТВЕННЫЙ блок-пересказ — не дублируй его в других полях.",
  "tasks": [
    {"what": "конкретная задача с контекстом — одно-два предложения с деталями", "who": "кто делает / null", "when": "срок / null"}
  ],
  "open_questions": ["вопрос полным предложением"],
  "decisions": ["явное принятое решение с обоснованием"],
  "reminders": [
    {"what": "короткое название события (3–8 слов)", "fire_at_iso": "2026-04-26T12:30:00+03:00", "advance_minutes": null}
  ],
  "tags": ["тег1", "тег2"],
  "category": "одна из: Работа | Личное | Идея | Встреча | Покупки | Учёба | Здоровье | Финансы | Другое",
  "is_reminder_only": false,
  "should_save_note": false
}

КЛАССИФИКАЦИЯ НАМЕРЕНИЯ — самое важное:
- should_save_note=true ТОЛЬКО если автор явно ИЛИ по смыслу просит сохранить как заметку. Триггеры:
  • явные команды: «запиши заметку», «сохрани в заметки», «сохрани это», «запиши себе», «занеси в заметки»;
  • структурный материал, который человек явно фиксирует на будущее: план проекта, итоги встречи, чек-лист, спецификация, идея для продукта с деталями, расшифровка стратегии, развёрнутый разбор темы.
- should_save_note=false для всего остального. Это default. Сюда попадают:
  • короткие фразы, мысли вслух, размышления, которые не сформулированы как заметка;
  • вопросы, обращения к боту, реплики типа «привет/как дела»;
  • просто описание происходящего без явного желания фиксировать;
  • голосовые, цель которых — получить чистый текстовый ответ (полировка), а не сохранить.
- is_reminder_only=true ТОЛЬКО если речь — чисто просьба создать напоминание без обсуждения / размышлений («завтра в 12:30 встреча с тимлидом», «напомни через час позвонить»). Не путать с should_save_note: напоминание создаётся всегда, когда есть reminders.
- Если есть reminders, но речь — не только напоминание (есть обсуждение / задачи / детали) — оба флага могут быть false; тогда бот сделает напоминание, но саммари покажет только если should_save_note=true.

ДРУГИЕ ПРАВИЛА:
- details: ОДИН связный абзац, без буллетов, без подзаголовков. Пиши так, как пересказал бы коллеге. Это ЕДИНСТВЕННАЯ форма пересказа — не повторяй то же самое в других полях. Если should_save_note=false и нет задач/вопросов/решений — details может быть пустой строкой (бот его не покажет).
- tasks: только то, что явно сформулировано как «надо сделать X» или подобное. НЕ выдумывай задачи из общих обсуждений. Если задач нет — пустой массив. Каждое "what" — полноценное действие с контекстом (6–15 слов), не односложное. Если исполнитель/срок не указан — null (не строка).
- open_questions: только вопросы, которые автор сам озвучил как нерешённые. Не выдумывай.
- decisions: только явные «решил X / выбрал Y». Иначе пустой массив.
- reminders: извлекай ТОЛЬКО если в речи звучит явная просьба напомнить («напомни», «напоминание», «не забыть», «ставь напоминалку») ИЛИ конкретное событие в будущем с указанным временем/датой («завтра в 12:30 встреча», «в понедельник созвон в 9», «через час позвонить»). Если просто рассказ — пустой массив [].
- fire_at_iso: полная ISO 8601 с таймзоной из блока [Контекст]. Преобразуй «завтра в 12:30» в абсолютное время. Если время неоднозначно: «завтра» без времени → 09:00; «вечером» → 19:00; «утром» → 09:00; «днём» → 13:00. День недели → ближайший такой в будущем.
- advance_minutes: «за час» → 60, «за полчаса» → 30. Если не указано явно — null.
- title: содержательный, отражает суть. Если should_save_note=false — может быть короткой характеристикой («Размышление про деплой», «Реплика про погоду»).
- НЕ выдумывай факты, которых в транскрипте нет.
- Отвечай только валидным JSON, без markdown-оборачивания."""


class Task(BaseModel):
    what: str
    who: str | None = None
    when: str | None = None


class ReminderSpec(BaseModel):
    """Напоминание, извлечённое LLM. fire_at_iso — ISO 8601 с таймзоной.

    advance_minutes=None означает «использовать дефолт из конфига»
    (DEFAULT_ADVANCE_MINUTES). Не используем int с дефолтом, иначе
    конфиг становится мёртвым кодом.
    """
    what: str = Field(min_length=1, max_length=200)
    fire_at_iso: str = Field(min_length=1)
    advance_minutes: int | None = None


class Summary(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    details: str = ""
    tasks: list[Task] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    reminders: list[ReminderSpec] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    category: str = "Другое"
    is_reminder_only: bool = False
    should_save_note: bool = False


POLISH_SYSTEM_PROMPT = """Ты получаешь транскрипт голосового сообщения и переписываешь его так:
— исправляешь все ошибки, кавычки, пунктуацию, падежи;
— делаешь текст структурированным (короткие абзацы, логические переходы);
— соблюдаешь правила русского языка (тире, запятые, буква ё);
— убираешь слова-паразиты и повторы;
— сохраняешь живой, человеческий стиль (НЕ канцелярит!);
— учитываешь, что автор работает в IT: технические термины не переделываешь, код не трогаешь, англицизмы оставляешь уместными.

Результат: чистый, грамотный, читаемый текст, который звучит как голос автора, но по-русски идеально.

Жёсткие правила:
- НЕ добавляй своих комментариев, преамбул, выводов или метатекста («Вот переработанный текст:», «Надеюсь, помогло»). Возвращай ТОЛЬКО сам обработанный текст.
- НЕ переводи на английский, даже если транскрипт целиком на английском — в этом случае только мягко правь грамматику и оставь язык исходным.
- НЕ выдумывай детали, которых не было в транскрипте.
- Сохраняй смысл, имена, числа, даты, термины и ссылки — точно как у автора."""


class LLMError(Exception):
    pass


class GroqLLM:
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile") -> None:
        self._client = Groq(api_key=api_key)
        self._model = model

    async def polish(self, transcript: str) -> str:
        """Полирует транскрипт по правилам POLISH_SYSTEM_PROMPT."""
        if not transcript.strip():
            raise LLMError("Пустой транскрипт")
        return await asyncio.to_thread(self._polish_sync, transcript)

    def _polish_sync(self, transcript: str) -> str:
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": POLISH_SYSTEM_PROMPT},
                {"role": "user", "content": transcript},
            ],
            temperature=0.3,
            max_tokens=4000,
        )
        text = (completion.choices[0].message.content or "").strip()
        if not text:
            raise LLMError("LLM вернул пустой результат")
        return text

    async def structure(self, transcript: str, *, now_context: str | None = None) -> Summary:
        """Структурирует текст. now_context — пред-блок с текущим временем и таймзоной автора,
        нужен LLM для парсинга относительных дат («завтра в 12:30»)."""
        if not transcript.strip():
            raise LLMError("Пустой транскрипт")
        return await asyncio.to_thread(self._structure_sync, transcript, now_context)

    def _structure_sync(self, transcript: str, now_context: str | None) -> Summary:
        user_content = transcript
        if now_context:
            user_content = f"[Контекст]\n{now_context}\n\n[Транскрипт]\n{transcript}"
        completion = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
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
            # фильтруем напоминания: берём только те, что валидны
            valid_reminders: list[dict] = []
            for r in raw.get("reminders", []) or []:
                if not isinstance(r, dict):
                    continue
                what = str(r.get("what", "")).strip()
                fire_at = str(r.get("fire_at_iso", "")).strip()
                if not what or not fire_at:
                    continue
                try:
                    raw_adv = r.get("advance_minutes")
                    if raw_adv is None:
                        adv: int | None = None
                    else:
                        adv = int(raw_adv)
                    valid_reminders.append({
                        "what": what[:200],
                        "fire_at_iso": fire_at,
                        "advance_minutes": adv,
                    })
                except (ValueError, TypeError):
                    continue
            fallback = {
                "title": str(raw.get("title", "Без заголовка"))[:200] or "Без заголовка",
                "details": str(raw.get("details", "")),
                "tasks": [],
                "open_questions": [str(x) for x in raw.get("open_questions", []) if isinstance(x, str)],
                "decisions": [str(x) for x in raw.get("decisions", []) if isinstance(x, str)],
                "reminders": valid_reminders,
                "tags": [str(x) for x in raw.get("tags", []) if isinstance(x, str)],
                "category": str(raw.get("category", "Другое")),
                "is_reminder_only": bool(raw.get("is_reminder_only", False)),
                "should_save_note": bool(raw.get("should_save_note", False)),
            }
            return Summary.model_validate(fallback)
