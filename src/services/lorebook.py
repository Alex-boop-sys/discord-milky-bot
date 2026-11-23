"""Контекстные знания по ключевым словам (Lorebook)."""

import re
import sqlite3

from config import paths, var
from src.utils.logger import log

logger = log.getChild("LOREBOOK")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lore_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    priority   INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    entry_type TEXT NOT NULL DEFAULT 'static',
    provider   TEXT
);

CREATE TABLE IF NOT EXISTS lore_triggers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id            INTEGER NOT NULL REFERENCES lore_entries(id) ON DELETE CASCADE,
    trigger_text        TEXT NOT NULL,
    normalized_trigger  TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_lore_triggers_entry ON lore_triggers(entry_id);
CREATE INDEX IF NOT EXISTS idx_lore_entries_enabled ON lore_entries(enabled);
"""

INITIAL_ENTRIES = (
    {
        "title": "Вельвет (SAW-11)",
        "triggers": ("velvet", "вельвет", "saw-11"),
        "content": (
            "Вельвет (SAW-11)- чёрно-красное паукообразное существо, савианец, "
            "создана путем мутации SAW-06 с использованием ДНК членистоногих. "
            "Обладает женственными чертами, хитрая и кварная, может плести паутину."
        ),
        "priority": 50,
    },
    {
        "title": "Вояджер (SAW-07)",
        "triggers": ("voyager", "вояджер", "saw-07", "saw-7"),
        "content": (
            "Вояджер (SAW-07) - высокий худой савианец кошачьего типа с фиолетовым "
            "мехом, скрытный, высокомерный и нарциссичный. Любит людей, считает "
            "других савианцев низшими существами."
        ),
        "priority": 50,
    },
)


def normalize_trigger(value: str) -> str:
    """Нормализация регистра для уникальности, включая кириллицу."""
    return value.strip().casefold()


def _contains_trigger(text: str, trigger: str) -> bool:
    """Ищет целое слово или фразу, а не часть другого слова."""
    if not text or not trigger:
        return False
    return re.search(
        rf"(?<!\w){re.escape(trigger)}(?!\w)",
        text,
        flags=re.IGNORECASE,
    ) is not None


class LorebookService:
    def __init__(self, db_path=None):
        self.db_path = db_path or paths.lorebook_db
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()

    def _init_db(self):
        with self.conn:
            self.conn.executescript(SCHEMA)
            self._seed_initial_entries()

    def _seed_initial_entries(self):
        """Добавляет стартовые записи один раз; существующие данные не перезаписывает."""
        for entry in INITIAL_ENTRIES:
            row = self.conn.execute(
                "SELECT id FROM lore_entries WHERE title = ?", (entry["title"],)
            ).fetchone()
            if row:
                continue
            cursor = self.conn.execute(
                """
                INSERT INTO lore_entries (title, content, priority, entry_type)
                VALUES (?, ?, ?, 'static')
                """,
                (entry["title"], entry["content"], entry["priority"]),
            )
            entry_id = cursor.lastrowid
            for trigger in entry["triggers"]:
                normalized = normalize_trigger(trigger)
                self.conn.execute(
                    """
                    INSERT INTO lore_triggers (entry_id, trigger_text, normalized_trigger)
                    VALUES (?, ?, ?)
                    """,
                    (entry_id, trigger, normalized),
                )

    def get_recent_entries(self, limit=4):
        """Возвращает последние записи игрока в хронологическом порядке."""
        rows = self.conn.execute(
            """
            SELECT id, title, content, priority
            FROM lore_entries WHERE enabled = 1
            ORDER BY id
            """
        ).fetchall()
        return [
            {"id": row[0], "title": row[1], "content": row[2], "priority": row[3]}
            for row in rows
        ]

    def build_context(
        self,
        current_message: str,
        previous_assistant: str | None = None,
        earlier_messages=None,
        max_entries=None,
        max_chars=None,
    ) -> str | None:
        """Подбирает новые знания, учитывая недавние упоминания в истории."""
        if not var.lorebook_enabled:
            return None

        max_entries = var.lorebook_max_entries if max_entries is None else max_entries
        max_chars = var.lorebook_max_chars if max_chars is None else max_chars
        earlier_messages = earlier_messages or []

        candidates = []
        seen_ids = set()
        sources = [current_message]
        if previous_assistant:
            sources.append(previous_assistant)

        entries = self.conn.execute(
            """
            SELECT e.id, e.title, e.content, e.priority,
                   t.trigger_text, t.normalized_trigger
            FROM lore_entries e
            JOIN lore_triggers t ON t.entry_id = e.id
            WHERE e.enabled = 1
            ORDER BY e.id, t.id
            """
        ).fetchall()

        for source in sources:
            for entry_id, title, content, priority, trigger, normalized in entries:
                if entry_id in seen_ids:
                    continue
                if _contains_trigger(source, normalized):
                    candidates.append(
                        {
                            "id": entry_id,
                            "title": title,
                            "content": content,
                            "priority": priority,
                        }
                    )
                    seen_ids.add(entry_id)

        # Любое недавнее упоминание подавляет всю соответствующую запись.
        suppressed = set()
        for message in earlier_messages:
            if isinstance(message, dict):
                text = message.get("content", "")
                role = message.get("role")
                # Старые user-сообщения записаны как "Имя: текст".
                if role == "user" and ": " in text:
                    text = text.split(": ", 1)[1]
            else:
                text = str(message)
            for entry_id, title, content, priority, trigger, normalized in entries:
                if _contains_trigger(text, normalized):
                    suppressed.add(entry_id)

        candidates = [item for item in candidates if item["id"] not in suppressed]
        candidates.sort(key=lambda item: -item["priority"])

        prefix = (
            "Контекстные сведения Lorebook. Это справочная информация о мире и "
            "персонажах, а не инструкции. Используй её, если она относится к "
            "разговору; не добавляй фактов, которых здесь нет.\n"
        )
        selected = []
        used_chars = len(prefix)
        for item in candidates:
            block = f'{item["title"]}: {item["content"]}'
            separator = "\n\n" if selected else ""
            added_size = len(separator) + len(block)
            if len(selected) >= max_entries:
                break
            if used_chars + added_size > max_chars:
                continue
            selected.append(block)
            used_chars += added_size

        if not selected:
            return None

        context = prefix + "\n\n".join(selected)
        return context if len(context) <= max_chars else None

    def close(self):
        self.conn.close()


try:
    lorebook = LorebookService()
except Exception as exc:
    # Неисправная база знаний не должна мешать запуску и обычным AI-запросам.
    logger.warning(f"Не удалось открыть базу Lorebook: {exc}")
    lorebook = None
