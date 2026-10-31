# src/features/economy.py
"""Экономика сервера: геофрукты за подтверждённые успешные бампы."""

import os
import re
import sqlite3
import time
from contextlib import contextmanager

from config import paths, var
from src.utils.logger import log

# Баланс общий для всех серверов с экономикой и хранится под одним ключом - ID
# основного сервера (так данные, накопленные раньше, остались на месте).
ECONOMY_KEY = var.main_guild_id
# Боты, чьи успешные бампы учитываем.
BUMP_BOT_IDS = {
    575776004233232386,  # DSMonitoring
    302050872383242240,  # DISBOARD
    235148962103951360,  # Carl-bot
    1159147139960676422,  # Discodus
    315926021457051650,  # Server Monitoring
}
# Эти два бота не всегда сообщают, кто именно сделал бамп.
# Для них запускаем минутный сбор реакций 🫐.
REACTION_BUMP_BOT_IDS = {
    235148962103951360,  # Carl-bot
    1159147139960676422,  # Discodus
}
BUMP_REWARD = 1
CURRENCY_NAME = "Геофрукт"
CURRENCY_EMOJI = "🫐"
REACTION_WINDOW_SECONDS = 60


class Economy:
    def __init__(self):
        os.makedirs(paths.data, exist_ok=True)
        self.db_path = paths.economy_db
        self._init_db()

    @contextmanager
    def _connect(self):
        """Короткое подключение к БД: коммит при успехе, откат при ошибке.

        В отличие от обычного `with sqlite3.connect(...)`, соединение ещё и
        закрывается в конце блока.
        """
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def enabled_in(guild_id):
        """Работает ли экономика на этом сервере (список economy_guild_ids в config.py)."""
        return guild_id in var.economy_guild_ids

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS balances (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bump_log (
                    message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    bump_bot_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reward INTEGER NOT NULL
                )
            """)
            # Отдельный журнал событий с реакциями. Он позволяет пережить рестарт
            # и не запускать повторную раздачу за то же сообщение подтверждения.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bump_claim_events (
                    source_message_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    bump_bot_id INTEGER NOT NULL,
                    reaction_message_id INTEGER,
                    started_at REAL,
                    ends_at REAL,
                    status TEXT NOT NULL DEFAULT 'queued'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bump_claim_participants (
                    source_message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reacted_at REAL NOT NULL,
                    PRIMARY KEY (source_message_id, user_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bump_claim_awards (
                    source_message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reward INTEGER NOT NULL,
                    PRIMARY KEY (source_message_id, user_id)
                )
            """)

    @staticmethod
    def _message_user_id(message):
        """Получает инициатора только из достоверных данных сообщения."""
        # В некоторых версиях Discord API ответ на slash-команду содержит
        # interaction_metadata с пользователем, вызвавшим команду.
        metadata = getattr(message, "interaction_metadata", None)
        user = getattr(metadata, "user", None) if metadata else None
        user_id = getattr(user, "id", None)
        if user_id:
            return int(user_id)

        # Совместимость с объектами/версиями discord.py, где доступно interaction.
        interaction = getattr(message, "interaction", None)
        user = getattr(interaction, "user", None) if interaction else None
        user_id = getattr(user, "id", None)
        if user_id:
            return int(user_id)

        # Некоторые боты явно упоминают пользователя в тексте подтверждения.
        match = re.search(r"<@!?(\d{15,22})>", message.content or "")
        if match:
            return int(match.group(1))
        for embed in getattr(message, "embeds", []):
            for text in (
                getattr(getattr(embed, "author", None), "name", ""),
                getattr(getattr(embed, "description", None), "text", ""),
                getattr(embed, "description", "") or "",
                getattr(getattr(embed, "footer", None), "text", ""),
            ):
                match = re.search(r"<@!?(\d{15,22})>", text or "")
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _is_success_message(message):
        """Проверяет известные формулировки успешного бампа."""
        content = (message.content or "").casefold()
        embed_text = " ".join(
            " ".join(
                filter(
                    None,
                    [
                        getattr(embed, "title", None),
                        getattr(embed, "description", None),
                        getattr(getattr(embed, "author", None), "name", None),
                        getattr(getattr(embed, "footer", None), "text", None),
                    ],
                )
            )
            for embed in getattr(message, "embeds", [])
        ).casefold()
        text = f"{content} {embed_text}"

        # Проверяем успех, а не просто любое сообщение от bump-бота.
        success_phrases = (
            "вы успешно лайкнули сервер",  # DSMonitoring
            "bump done",  # DISBOARD / Discodus
            "you've successfully bumped this server",  # Carl-bot
            "server bumped by",  # Server Monitoring
        )
        return any(phrase in text for phrase in success_phrases)

    def is_reaction_bump_success(self, message):
        """True для подтверждения успешного бампа Carl-bot или Discodus."""
        return bool(
            message.guild
            and self.enabled_in(message.guild.id)
            and message.author.bot
            and message.author.id in REACTION_BUMP_BOT_IDS
            and self._is_success_message(message)
        )

    def reserve_reaction_event(self, message):
        """Резервирует событие, не позволяя обработать подтверждение дважды."""
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO bump_claim_events
                   (source_message_id, guild_id, channel_id, bump_bot_id, status)
                   VALUES (?, ?, ?, ?, 'queued')""",
                (message.id, message.guild.id, message.channel.id, message.author.id),
            )
            return cursor.rowcount == 1

    def activate_reaction_event(self, source_message_id, reaction_message_id):
        started_at = time.time()
        ends_at = started_at + REACTION_WINDOW_SECONDS
        with self._connect() as conn:
            conn.execute(
                """UPDATE bump_claim_events
                   SET reaction_message_id = ?, started_at = ?, ends_at = ?, status = 'active'
                   WHERE source_message_id = ? AND status = 'queued'""",
                (reaction_message_id, started_at, ends_at, source_message_id),
            )
        return ends_at

    def fail_reaction_event(self, source_message_id):
        with self._connect() as conn:
            conn.execute(
                "UPDATE bump_claim_events SET status = 'failed' WHERE source_message_id = ? AND status IN ('queued', 'active')",
                (source_message_id,),
            )

    def get_active_reaction_events(self):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT source_message_id, guild_id, channel_id,
                          reaction_message_id, ends_at
                   FROM bump_claim_events WHERE status = 'active'"""
            ).fetchall()
        return [
            {
                "source_message_id": row[0],
                "guild_id": row[1],
                "channel_id": row[2],
                "reaction_message_id": row[3],
                "ends_at": row[4],
            }
            for row in rows
        ]

    def get_queued_reaction_events(self):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT source_message_id, guild_id, channel_id
                   FROM bump_claim_events WHERE status = 'queued'
                   ORDER BY source_message_id"""
            ).fetchall()
        return [
            {"source_message_id": row[0], "guild_id": row[1], "channel_id": row[2]}
            for row in rows
        ]

    def add_reaction_participant(
        self, reaction_message_id, user_id, guild_id, emoji_name
    ):
        """Запоминает первого участника, поставившего нужную реакцию до дедлайна."""
        if emoji_name != CURRENCY_EMOJI or not self.enabled_in(guild_id):
            return False
        now = time.time()
        with self._connect() as conn:
            event = conn.execute(
                """SELECT source_message_id FROM bump_claim_events
                   WHERE reaction_message_id = ? AND guild_id = ?
                     AND status = 'active' AND ends_at > ?""",
                (reaction_message_id, guild_id, now),
            ).fetchone()
            if not event:
                return False
            cursor = conn.execute(
                """INSERT OR IGNORE INTO bump_claim_participants
                   (source_message_id, user_id, reacted_at) VALUES (?, ?, ?)""",
                (event[0], user_id, now),
            )
            return cursor.rowcount == 1

    def finish_reaction_event(self, source_message_id):
        """Закрывает событие и атомарно выдаёт по одному Геофрукту участникам."""
        with self._connect() as conn:
            event = conn.execute(
                "SELECT guild_id, status FROM bump_claim_events WHERE source_message_id = ?",
                (source_message_id,),
            ).fetchone()
            if not event or event[1] != "active":
                return None

            participants = conn.execute(
                "SELECT user_id FROM bump_claim_participants WHERE source_message_id = ? ORDER BY reacted_at, user_id",
                (source_message_id,),
            ).fetchall()
            user_ids = [row[0] for row in participants]

            for user_id in user_ids:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO bump_claim_awards
                       (source_message_id, user_id, reward) VALUES (?, ?, ?)""",
                    (source_message_id, user_id, BUMP_REWARD),
                )
                if cursor.rowcount:
                    conn.execute(
                        """INSERT INTO balances (guild_id, user_id, balance)
                           VALUES (?, ?, ?)
                           ON CONFLICT(guild_id, user_id)
                           DO UPDATE SET balance = balance + excluded.balance""",
                        (ECONOMY_KEY, user_id, BUMP_REWARD),
                    )
            conn.execute(
                "UPDATE bump_claim_events SET status = 'completed' WHERE source_message_id = ?",
                (source_message_id,),
            )
        return user_ids

    def process_bump_message(self, message):
        """Начисляет прямую награду за сообщение, где инициатор известен.

        Carl-bot и Discodus обрабатываются отдельным сбором реакций.
        """
        if not message.guild or not self.enabled_in(message.guild.id):
            return None
        if not message.author.bot or message.author.id not in BUMP_BOT_IDS:
            return None
        if message.author.id in REACTION_BUMP_BOT_IDS:
            return None
        if not self._is_success_message(message):
            return None

        user_id = self._message_user_id(message)
        if user_id is None:
            log.warning(
                f"Успешный бамп от бота {message.author.id} (сообщение {message.id}), "
                "но инициатор не указан в сообщении; награда не начислена"
            )
            return None

        # Один ID сообщения может быть обработан только один раз.
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO bump_log
                   (message_id, guild_id, user_id, bump_bot_id, channel_id, reward)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    message.id,
                    message.guild.id,
                    user_id,
                    message.author.id,
                    message.channel.id,
                    BUMP_REWARD,
                ),
            )
            if cursor.rowcount == 0:
                return None
            conn.execute(
                """INSERT INTO balances (guild_id, user_id, balance)
                   VALUES (?, ?, ?)
                   ON CONFLICT(guild_id, user_id)
                   DO UPDATE SET balance = balance + excluded.balance""",
                (ECONOMY_KEY, user_id, BUMP_REWARD),
            )

        log.info(
            f"Начислен {BUMP_REWARD} Геофрукт пользователю {user_id} "
            f"за бамп (бот {message.author.id}, сообщение {message.id})"
        )
        return user_id

    @staticmethod
    def currency_word(count):
        """Возвращает правильную форму слова «Геофрукт»."""
        last_two = count % 100
        last_one = count % 10
        if 11 <= last_two <= 14:
            return "Геофруктов"
        if last_one == 1:
            return "Геофрукт"
        if 2 <= last_one <= 4:
            return "Геофрукта"
        return "Геофруктов"

    def get_balance(self, user_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT balance FROM balances WHERE guild_id = ? AND user_id = ?",
                (ECONOMY_KEY, user_id),
            ).fetchone()
        return row[0] if row else 0

    def get_top(self, limit=10):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT user_id, balance FROM balances
                   WHERE guild_id = ? AND balance > 0
                   ORDER BY balance DESC, user_id ASC LIMIT ?""",
                (ECONOMY_KEY, limit),
            ).fetchall()
        return [{"user_id": row[0], "balance": row[1]} for row in rows]


economy = Economy()
