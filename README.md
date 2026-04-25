# Secretary Bot 🤖📝

Telegram-бот-секретарь: пришлите голосовое сообщение — получите структурированную заметку с заголовком, саммари, задачами, тегами.

Работает бесплатно на [Groq Cloud](https://console.groq.com/) (Whisper-large-v3-turbo для распознавания речи + Llama 3.3 70B для структурирования).

## Возможности

- 🎤 Приём голосовых и аудиофайлов Telegram (до 25 МБ).
- 🧠 Распознавание речи (русский/английский автоматически) и структурирование в JSON.
- 📌 Автоматический заголовок, саммари в пунктах, извлечение задач (что/кто/когда).
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
