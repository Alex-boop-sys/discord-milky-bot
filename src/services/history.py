"""Хранение истории диалогов и статистики в SQLite.

Две таблицы с разными задачами:
  history    - контекст для ИИ. Хранит только последние var.max_history записей
               каждого игрока, остальное удаляется. /забыть удаляет её целиком.
  user_stats - счётчик обращений к боту. Только растёт, история на него не влияет.
"""

import os
import sqlite3

from config import paths, var
from src.utils.json_parser import dicts

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT    NOT NULL CHECK (role IN ('user', 'assistant')),
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history (user_id, id);

CREATE TABLE IF NOT EXISTS user_stats (
    user_id       INTEGER PRIMARY KEY,
    message_count INTEGER NOT NULL DEFAULT 0
);
"""


class HistoryService:
    def __init__(self):
        self.db_path = paths.db_path
        os.makedirs(paths.data, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._init_db()

    def _init_db(self):
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        has_tables = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'history'"
        ).fetchone()

        # База от старой версии бота: не работаем с ней, чтобы ничего не испортить.
        if has_tables and version != SCHEMA_VERSION:
            self.conn.close()
            raise RuntimeError(
                f"База {self.db_path} имеет старый формат (версия {version}, "
                f"нужна {SCHEMA_VERSION}). Сначала запусти scripts/migrate_history.py"
            )

        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def _prune(self, user_id):
        """Удаляет всё, что старше последних var.max_history записей игрока."""
        self.conn.execute(
            """
            DELETE FROM history
            WHERE user_id = ?
              AND id <= (
                  SELECT id FROM history
                  WHERE user_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?
              )
            """,
            (user_id, user_id, var.max_history),
        )

    def build_messages(self, user_id, user_name, message):
        enriched = f"{user_name}: {message}"
        # Сообщение, счётчик и чистка старого - одной транзакцией.
        with self.conn:
            self.conn.execute(
                "INSERT INTO history (user_id, role, content) VALUES (?, 'user', ?)",
                (user_id, enriched),
            )
            self.conn.execute(
                """
                INSERT INTO user_stats (user_id, message_count) VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET message_count = message_count + 1
                """,
                (user_id,),
            )
            self._prune(user_id)

        rows = self.conn.execute(
            "SELECT role, content FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, var.max_history),
        ).fetchall()
        history = [
            {"role": role, "content": content} for role, content in reversed(rows)
        ]
        return [{"role": "system", "content": dicts.get("def_prompt")}] + history

    def save_response(self, user_id, text):
        with self.conn:
            self.conn.execute(
                "INSERT INTO history (user_id, role, content) VALUES (?, 'assistant', ?)",
                (user_id, text),
            )
        # Экранируем звёздочки для Discord, как и раньше.
        return text.replace("*", "\\*")

    def clear_history(self, user_id):
        """Физически удаляет историю игрока. Счётчик (user_stats) не трогаем."""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM history WHERE user_id = ?", (user_id,)
            )
        return cursor.rowcount > 0

    def get_top_users(self, limit=10):
        rows = self.conn.execute(
            "SELECT user_id, message_count FROM user_stats ORDER BY message_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"user_id": user_id, "count": count} for user_id, count in rows]


history = HistoryService()
