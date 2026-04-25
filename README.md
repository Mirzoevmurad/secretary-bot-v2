# Secretary Bot 🤖📝

Telegram-бот-секретарь: пришлите голосовое или текст — получите структурированную заметку и автоматические напоминания, если в речи были даты/времена.

Работает бесплатно на [Groq Cloud](https://console.groq.com/) (Whisper-large-v3-turbo для распознавания речи + Llama 3.3 70B для структурирования).

## Возможности

- 🎤 Приём голосовых, аудиофайлов (до 25 МБ) и текстовых сообщений.
- 🧠 Распознавание речи (русский/английский автоматически) и структурирование в JSON.
- 📌 Заголовок, TL;DR, развёрнутый пересказ, саммари в пунктах, задачи (что/кто/когда), решения, открытые вопросы.
- ⏰ **Напоминания** — «завтра в 12:30 встреча с тимлидом» → бот пришлёт уведомление за 5 минут и в само событие.
- 📅 **iCal-экспорт** — выгрузка всех активных напоминаний файлом `.ics` для импорта в любой календарь.
- 🗂 Категория (Работа/Личное/Идея/Встреча/Покупки/…) и теги.
- 🔍 Полнотекстовый поиск по всем заметкам (`/search`).
- 📋 Список/просмотр/удаление заметок.
- 🔒 Whitelist по Telegram user_id: посторонние не пользуются.

## Команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/help` | Список команд |
| `/last` | Последняя заметка |
| `/list [N]` | Последние N заметок (по умолч. 10) |
| `/search <запрос>` | Поиск по транскриптам и заголовкам |
| `/get_<id>` | Транскрипт заметки целиком |
| `/delete_<id>` | Удалить заметку |
| `/reminders` | Активные напоминания |
| `/cancel_<id>` | Отменить напоминание |
| `/export_ical` | Скачать .ics-файл со всеми напоминаниями |
| `/stats` | Сколько всего заметок и часов аудио |
| `/lang ru\|en\|auto` | Язык распознавания по умолчанию |
| `/forget_all` | Удалить все свои заметки |

## Быстрый старт (локально)

```bash
git clone https://github.com/Mirzoevmurad/secretary-bot.git
cd secretary-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируйте .env: SECRETARY_BOT_TOKEN, GROQ_API_KEY, ALLOWED_USER_IDS
python bot.py
```

Требования: Python 3.10+, ffmpeg в PATH.

## Получить ключи

1. **Telegram-бот** — [@BotFather](https://t.me/BotFather) → `/newbot` → скопировать токен → `SECRETARY_BOT_TOKEN`.
2. **Groq API** — https://console.groq.com/keys (вход через Google, карта не нужна) → Create API Key → `GROQ_API_KEY`.
3. **Ваш user_id** — написать [@userinfobot](https://t.me/userinfobot) → `ALLOWED_USER_IDS`.

## Переменные окружения

| Переменная | Обязательная | По умолчанию | Описание |
|---|---|---|---|
| `SECRETARY_BOT_TOKEN` | да | — | Токен бота от @BotFather |
| `GROQ_API_KEY` | да | — | Ключ Groq Cloud |
| `ALLOWED_USER_IDS` | да | — | Через запятую, user_id'ы в whitelist |
| `DB_PATH` | нет | `data/secretary.sqlite` | Путь к SQLite |
| `STT_MODEL` | нет | `whisper-large-v3-turbo` | Модель распознавания |
| `LLM_MODEL` | нет | `llama-3.3-70b-versatile` | Модель структурирования |
| `DEFAULT_LANG` | нет | `auto` | Язык распознавания по умолчанию |
| `MAX_AUDIO_MB` | нет | `25` | Лимит размера (Groq API сам лимитит 25 МБ) |
| `KEEP_AUDIO` | нет | `false` | Хранить ли оригинал аудио на диске |
| `TZ_NAME` | нет | `Europe/Moscow` | Часовой пояс автора (IANA) — для парсинга дат |
| `DEFAULT_ADVANCE_MINUTES` | нет | `5` | За сколько минут уведомлять до события по умолчанию |

## Тесты

```bash
pip install pytest
pytest tests/
```

## Деплой на VPS

См. [DEPLOYMENT.md](DEPLOYMENT.md).

## Архитектура

```
voice → ffmpeg (→ wav 16kHz mono) → Groq Whisper → транскрипт
                                           ↓
                                     Groq Llama 3.3
                                           ↓
                                     JSON {title, summary[], tasks[], tags[], category}
                                           ↓
                                     SQLite (notes + FTS5)
                                           ↓
                                     Telegram HTML-ответ
```

## Приватность

- Аудиофайлы по умолчанию удаляются сразу после обработки (`KEEP_AUDIO=false`).
- Транскрипты и саммари хранятся в локальной SQLite на вашем VPS.
- Whisper и LLM хостятся Groq → их серверы видят содержимое. Для полной автономности см. ТЗ (локальный Whisper + Llama), но нужен GPU-сервер.
- `/forget_all` стирает все заметки пользователя.
