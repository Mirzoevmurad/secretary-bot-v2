"""SQLite-хранилище: заметки + FTS-поиск."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    lang TEXT DEFAULT 'auto',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    duration_seconds REAL,
    lang TEXT,
    title TEXT,
    transcript TEXT NOT NULL,
    summary_json TEXT NOT NULL,       -- {summary: [...], tasks: [...], tags: [...], open_questions: [...]}
    audio_path TEXT,                  -- NULL если аудио удалено после обработки
    source TEXT DEFAULT 'voice',      -- 'voice' | 'audio' (форвард)
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_notes_user_created ON notes(user_id, created_at DESC);

-- FTS5 для поиска по транскрипту + заголовку
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title,
    transcript,
    content='notes',
    content_rowid='note_id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, transcript) VALUES (new.note_id, new.title, new.transcript);
END;
CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, transcript) VALUES('delete', old.note_id, old.title, old.transcript);
END;
CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, transcript) VALUES('delete', old.note_id, old.title, old.transcript);
    INSERT INTO notes_fts(rowid, title, transcript) VALUES (new.note_id, new.title, new.transcript);
END;

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    fire_at INTEGER NOT NULL,                 -- epoch UTC секунд
    advance_minutes INTEGER NOT NULL DEFAULT 0,
    text TEXT NOT NULL,
    source_note_id INTEGER,                   -- если напоминание создано из заметки
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | fired | cancelled
    fired_at INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_reminders_user_status ON reminders(user_id, status, fire_at);
CREATE INDEX IF NOT EXISTS idx_reminders_fire_at ON reminders(fire_at);
"""


@dataclass(slots=True)
class Note:
    note_id: int
    user_id: int
    created_at: int
    duration_seconds: float | None
    lang: str | None
    title: str
    transcript: str
    summary: dict
    audio_path: str | None
    source: str


@dataclass(slots=True)
class Reminder:
    reminder_id: int
    user_id: int
    created_at: int
    fire_at: int
    advance_minutes: int
    text: str
    source_note_id: int | None
    status: str
    fired_at: int | None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---- users ---------------------------------------------------------

    def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        now = int(time.time())
        with self._tx() as c:
            c.execute(
                """INSERT INTO users (user_id, username, first_name, last_name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                     username=excluded.username,
                     first_name=excluded.first_name,
                     last_name=excluded.last_name,
                     updated_at=excluded.updated_at
                """,
                (user_id, username, first_name, last_name, now, now),
            )

    def set_lang(self, user_id: int, lang: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE users SET lang=?, updated_at=? WHERE user_id=?", (lang, int(time.time()), user_id))

    def get_lang(self, user_id: int) -> str | None:
        row = self._conn.execute("SELECT lang FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row["lang"] if row else None

    # ---- notes ---------------------------------------------------------

    def add_note(
        self,
        *,
        user_id: int,
        transcript: str,
        summary: dict,
        title: str,
        lang: str | None,
        duration_seconds: float | None,
        audio_path: str | None,
        source: str,
    ) -> int:
        now = int(time.time())
        with self._tx() as c:
            cur = c.execute(
                """INSERT INTO notes (user_id, created_at, duration_seconds, lang, title, transcript, summary_json, audio_path, source)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    user_id,
                    now,
                    duration_seconds,
                    lang,
                    title,
                    transcript,
                    json.dumps(summary, ensure_ascii=False),
                    audio_path,
                    source,
                ),
            )
            return int(cur.lastrowid)

    def get_note(self, note_id: int, user_id: int) -> Note | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE note_id=? AND user_id=?",
            (note_id, user_id),
        ).fetchone()
        return _to_note(row) if row else None

    def last_note(self, user_id: int) -> Note | None:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE user_id=? ORDER BY created_at DESC, note_id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return _to_note(row) if row else None

    def list_notes(self, user_id: int, limit: int = 10) -> list[Note]:
        rows = self._conn.execute(
            "SELECT * FROM notes WHERE user_id=? ORDER BY created_at DESC, note_id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [_to_note(r) for r in rows]

    def delete_note(self, note_id: int, user_id: int) -> bool:
        with self._tx() as c:
            cur = c.execute("DELETE FROM notes WHERE note_id=? AND user_id=?", (note_id, user_id))
            return cur.rowcount > 0

    def update_note_title(self, note_id: int, user_id: int, new_title: str) -> bool:
        """Меняет title заметки и одновременно поле title в summary_json."""
        note = self.get_note(note_id, user_id)
        if note is None:
            return False
        new_summary = dict(note.summary)
        new_summary["title"] = new_title
        with self._tx() as c:
            cur = c.execute(
                "UPDATE notes SET title=?, summary_json=? WHERE note_id=? AND user_id=?",
                (new_title, json.dumps(new_summary, ensure_ascii=False), note_id, user_id),
            )
            return cur.rowcount > 0

    def update_note_summary_field(
        self, note_id: int, user_id: int, field: str, value
    ) -> bool:
        """Обновляет произвольное поле в summary_json (category/tags/...)."""
        note = self.get_note(note_id, user_id)
        if note is None:
            return False
        new_summary = dict(note.summary)
        new_summary[field] = value
        with self._tx() as c:
            cur = c.execute(
                "UPDATE notes SET summary_json=? WHERE note_id=? AND user_id=?",
                (json.dumps(new_summary, ensure_ascii=False), note_id, user_id),
            )
            return cur.rowcount > 0

    def delete_all(self, user_id: int) -> int:
        with self._tx() as c:
            cur = c.execute("DELETE FROM notes WHERE user_id=?", (user_id,))
            return cur.rowcount

    def search(self, user_id: int, query: str, limit: int = 10) -> list[Note]:
        q = _sanitize_fts(query)
        if not q:
            return []
        rows = self._conn.execute(
            """SELECT n.* FROM notes n
               JOIN notes_fts f ON f.rowid = n.note_id
               WHERE n.user_id = ? AND notes_fts MATCH ?
               ORDER BY n.created_at DESC LIMIT ?""",
            (user_id, q, limit),
        ).fetchall()
        return [_to_note(r) for r in rows]

    # ---- reminders -----------------------------------------------------

    def add_reminder(
        self,
        *,
        user_id: int,
        fire_at: int,
        advance_minutes: int,
        text: str,
        source_note_id: int | None,
    ) -> int:
        now = int(time.time())
        with self._tx() as c:
            cur = c.execute(
                """INSERT INTO reminders (user_id, created_at, fire_at, advance_minutes, text, source_note_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (user_id, now, fire_at, max(0, advance_minutes), text, source_note_id),
            )
            return int(cur.lastrowid)

    def get_reminder(self, reminder_id: int, user_id: int) -> Reminder | None:
        row = self._conn.execute(
            "SELECT * FROM reminders WHERE reminder_id=? AND user_id=?",
            (reminder_id, user_id),
        ).fetchone()
        return _to_reminder(row) if row else None

    def get_reminder_any(self, reminder_id: int) -> Reminder | None:
        """Без проверки user_id — для job-callback'ов, где user_id известен из самого reminder."""
        row = self._conn.execute(
            "SELECT * FROM reminders WHERE reminder_id=?",
            (reminder_id,),
        ).fetchone()
        return _to_reminder(row) if row else None

    def list_pending_reminders(self, user_id: int) -> list[Reminder]:
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE user_id=? AND status='pending' ORDER BY fire_at ASC",
            (user_id,),
        ).fetchall()
        return [_to_reminder(r) for r in rows]

    def all_pending_reminders(self) -> list[Reminder]:
        """Используется при старте бота, чтобы перепланировать все висящие задачи."""
        rows = self._conn.execute(
            "SELECT * FROM reminders WHERE status='pending' ORDER BY fire_at ASC",
        ).fetchall()
        return [_to_reminder(r) for r in rows]

    def mark_reminder_fired(self, reminder_id: int) -> None:
        with self._tx() as c:
            c.execute(
                "UPDATE reminders SET status='fired', fired_at=? WHERE reminder_id=? AND status='pending'",
                (int(time.time()), reminder_id),
            )

    def cancel_reminder(self, reminder_id: int, user_id: int) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE reminders SET status='cancelled' WHERE reminder_id=? AND user_id=? AND status='pending'",
                (reminder_id, user_id),
            )
            return cur.rowcount > 0

    def update_reminder_text(self, reminder_id: int, user_id: int, new_text: str) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE reminders SET text=? WHERE reminder_id=? AND user_id=? AND status='pending'",
                (new_text, reminder_id, user_id),
            )
            return cur.rowcount > 0

    def update_reminder_fire_at(self, reminder_id: int, user_id: int, new_fire_at: int) -> bool:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE reminders SET fire_at=? WHERE reminder_id=? AND user_id=? AND status='pending'",
                (new_fire_at, reminder_id, user_id),
            )
            return cur.rowcount > 0

    def prune_old_reminders(self, before_epoch: int) -> int:
        """Удаляет fired/cancelled напоминания старше before_epoch (по fired_at или fire_at)."""
        with self._tx() as c:
            cur = c.execute(
                """DELETE FROM reminders
                   WHERE status IN ('fired', 'cancelled')
                     AND COALESCE(fired_at, fire_at) < ?""",
                (before_epoch,),
            )
            return cur.rowcount

    # ---- stats ---------------------------------------------------------

    def stats(self, user_id: int) -> dict:
        row = self._conn.execute(
            """SELECT COUNT(*) AS total,
                      COALESCE(SUM(duration_seconds), 0) AS total_seconds,
                      MIN(created_at) AS first_at,
                      MAX(created_at) AS last_at
               FROM notes WHERE user_id=?""",
            (user_id,),
        ).fetchone()
        return {
            "total": row["total"],
            "total_seconds": float(row["total_seconds"] or 0.0),
            "first_at": row["first_at"],
            "last_at": row["last_at"],
        }


def _to_note(row: sqlite3.Row) -> Note:
    return Note(
        note_id=row["note_id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        duration_seconds=row["duration_seconds"],
        lang=row["lang"],
        title=row["title"],
        transcript=row["transcript"],
        summary=json.loads(row["summary_json"]),
        audio_path=row["audio_path"],
        source=row["source"],
    )


def _to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        reminder_id=row["reminder_id"],
        user_id=row["user_id"],
        created_at=row["created_at"],
        fire_at=row["fire_at"],
        advance_minutes=row["advance_minutes"],
        text=row["text"],
        source_note_id=row["source_note_id"],
        status=row["status"],
        fired_at=row["fired_at"],
    )


def _sanitize_fts(q: str) -> str:
    """Элементарная очистка запроса под FTS5: убираем служебные символы, оборачиваем в кавычки."""
    q = q.strip()
    if not q:
        return ""
    # экранируем двойные кавычки
    q = q.replace('"', '""')
    return f'"{q}"'
