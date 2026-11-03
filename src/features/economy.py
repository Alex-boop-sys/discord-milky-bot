# src/features/economy.py
"""Экономика сервера: геофрукты за подтверждённые успешные бампы."""

import os
import random
import re
import sqlite3
import time
import warnings
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
# Если Discord сам сообщает, кто вызвал команду бампа (interaction_metadata), награда
# выдаётся этому человеку напрямую, даже для Carl-bot и Discodus. Мини-игра с реакциями
# остаётся запасным вариантом, когда инициатор неизвестен. False - всегда мини-игра.
DIRECT_REWARD_FOR_REACTION_BOTS = True
# Источники, которым можно доверять при определении инициатора (упоминание в тексте - нет).
RELIABLE_INITIATOR_SOURCES = ("interaction_metadata", "interaction")

# Фразы успешного бампа (сравниваются после normalize_text, поэтому регистр, апострофы,
# Markdown и лишние пробелы значения не имеют).
SUCCESS_PHRASES = (
    "вы успешно лайкнули сервер",  # DSMonitoring
    "bump done",  # DISBOARD / Discodus
    "you've successfully bumped this server",  # Carl-bot
    "server bumped by",  # Server Monitoring
)
BUMP_REWARD = 1
CURRENCY_NAME = "Геофрукт"
CURRENCY_EMOJI = "🫐"
REACTION_WINDOW_SECONDS = 60

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "′": "'"})


def normalize_text(text):
    """Текст для поиска фраз: регистр, типографские апострофы, Markdown, эмодзи, пробелы."""
    text = (text or "").casefold().translate(_APOSTROPHES)
    text = re.sub(r"<a?:\w+:\d+>", " ", text)  # серверные эмодзи <:имя:123>
    text = re.sub(r"[*_~`|>]", "", text)  # Markdown-разметка
    return re.sub(r"\s+", " ", text).strip()


def legacy_interaction(message):
    """Устаревшее поле message.interaction (оставлено на случай, если Discord пришлёт только его).

    discord.py при обращении к нему сам принудительно показывает предупреждение, поэтому
    обычный фильтр не помогает: предупреждения складываем в «пустой» список (record=True).
    """
    with warnings.catch_warnings(record=True):
        return getattr(message, "interaction", None)


def _component_texts(components):
    """Тексты из компонентов сообщения, в том числе новых (v2: TextDisplay, секции, контейнеры)."""
    texts = []
    for component in components or []:
        content = getattr(component, "content", None)
        if isinstance(content, str):
            texts.append(content)
        for attr in ("children", "components"):
            nested = getattr(component, attr, None)
            if isinstance(nested, (list, tuple)):
                texts.extend(_component_texts(nested))
        accessory = getattr(component, "accessory", None)
        if accessory is not None:
            texts.extend(_component_texts([accessory]))
    return texts


def collect_text(message):
    """Весь видимый текст сообщения: обычный, из эмбедов (включая поля) и из компонентов."""
    parts = [message.content or ""]
    for embed in getattr(message, "embeds", None) or []:
        parts.extend(
            [
                getattr(embed, "title", None),
                getattr(embed, "description", None),
                getattr(getattr(embed, "author", None), "name", None),
                getattr(getattr(embed, "footer", None), "text", None),
            ]
        )
        for field in getattr(embed, "fields", None) or []:
            parts.extend([getattr(field, "name", None), getattr(field, "value", None)])
    parts.extend(_component_texts(getattr(message, "components", None)))
    return " ".join(part for part in parts if isinstance(part, str) and part)


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
            # Сохраняем результат случайного броска, чтобы повторные правки одного
            # сообщения не увеличивали вероятность запуска мини-ивента.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bump_reaction_event_rolls (
                    source_message_id INTEGER PRIMARY KEY,
                    should_start INTEGER NOT NULL
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
    def find_initiator(message):
        """Кто вызвал бамп: (ID, источник сведений) либо (None, None).

        Надёжные источники идут первыми: Discord сам указывает, кто вызвал команду
        (interaction_metadata / interaction). Упоминание в тексте - запасной вариант.
        """
        # В ответе на slash-команду Discord указывает пользователя, вызвавшего команду.
        metadata = getattr(message, "interaction_metadata", None)
        user_id = getattr(getattr(metadata, "user", None), "id", None) if metadata else None
        if user_id:
            return int(user_id), "interaction_metadata"

        # Более старое поле (в новых версиях Discord API считается устаревшим).
        interaction = legacy_interaction(message)
        user_id = getattr(getattr(interaction, "user", None), "id", None) if interaction else None
        if user_id:
            return int(user_id), "interaction"

        # Некоторые боты явно упоминают пользователя в тексте подтверждения.
        match = re.search(r"<@!?(\d{15,22})>", collect_text(message))
        if match:
            return int(match.group(1)), "mention"
        return None, None

    @classmethod
    def _message_user_id(cls, message):
        return cls.find_initiator(message)[0]

    @staticmethod
    def matched_phrase(message):
        """Какая из известных фраз успешного бампа есть в сообщении (или None)."""
        text = normalize_text(collect_text(message))
        for phrase in SUCCESS_PHRASES:
            if normalize_text(phrase) in text:
                return phrase
        return None

    @classmethod
    def _is_success_message(cls, message):
        """Проверяет известные формулировки успешного бампа (а не любое сообщение бота)."""
        return cls.matched_phrase(message) is not None

    def is_reaction_bump_success(self, message):
        """True для успешного бампа любого поддерживаемого бота."""
        return bool(
            message.guild
            and self.enabled_in(message.guild.id)
            and message.author.bot
            and message.author.id in BUMP_BOT_IDS
            and self._is_success_message(message)
        )

    def diagnose(self, message):
        """Объясняет, что Милки сделает с сообщением и почему. Ничего не меняет (ни базу, ни награды).

        Возвращает {"checks": [{"name", "ok", "detail"}, ...], "verdict", "summary"}.
        verdict: "reward" (награда напрямую), "minigame" (сбор реакций), "wait" (бот ещё
        «думает», результат придёт правкой) или "ignore".
        """
        guild = getattr(message, "guild", None)
        author = getattr(message, "author", None)
        author_id = getattr(author, "id", None)
        loading = bool(getattr(getattr(message, "flags", None), "loading", False))
        phrase = self.matched_phrase(message)
        user_id, source = self.find_initiator(message)
        economy_here = bool(guild and self.enabled_in(guild.id))
        is_bot = bool(getattr(author, "bot", False))
        known_bot = author_id in BUMP_BOT_IDS
        reaction_bot = author_id in REACTION_BUMP_BOT_IDS
        seen = normalize_text(collect_text(message))
        seen = seen if len(seen) <= 160 else seen[:159] + "…"

        checks = [
            {
                "name": "Сообщение готово (бот не «думает»)",
                "ok": not loading,
                "detail": "" if not loading else "флаг loading: итог придёт правкой сообщения",
            },
            {
                "name": "Сервер с экономикой",
                "ok": economy_here,
                "detail": f"сервер {getattr(guild, 'id', None)}; экономика включена на {list(var.economy_guild_ids)}",
            },
            {"name": "Автор - бот", "ok": is_bot, "detail": f"автор {author_id}"},
            {
                "name": "Бот известен экономике",
                "ok": known_bot,
                "detail": (
                    "режим: сбор реакций" if reaction_bot else "режим: награда напрямую"
                )
                if known_bot
                else f"ID {author_id} нет в BUMP_BOT_IDS",
            },
            {
                "name": "Есть фраза успешного бампа",
                "ok": phrase is not None,
                "detail": f"найдена «{phrase}»"
                if phrase
                else f"не найдена; Милки видит текст: «{seen}»",
            },
            {
                "name": "Известен инициатор бампа",
                "ok": user_id is not None,
                "detail": f"{user_id} (источник: {source})"
                if user_id
                else "нет ни в метаданных команды, ни в тексте",
            },
        ]

        if loading:
            verdict, summary = "wait", "сообщение ещё «загружается»: решение будет после его правки"
        elif not (economy_here and is_bot and known_bot and phrase):
            failed = next(c["name"] for c in checks[1:5] if not c["ok"])
            verdict, summary = "ignore", f"игнорирует: не выполнено «{failed}»"
        elif reaction_bot and not (
            DIRECT_REWARD_FOR_REACTION_BOTS and source in RELIABLE_INITIATOR_SOURCES
        ):
            verdict, summary = "minigame", "запустит сбор реакций 🫐 (инициатор неизвестен наверняка)"
        elif user_id:
            verdict, summary = "reward", f"начислит награду пользователю {user_id}"
        else:
            verdict, summary = "ignore", "игнорирует: бамп успешен, но инициатор неизвестен"
        return {"checks": checks, "verdict": verdict, "summary": summary}

    def is_rewarded(self, message_id):
        """Начислена ли уже прямая награда за это сообщение бота."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM bump_log WHERE message_id = ?", (message_id,)
            ).fetchone()
        return row is not None

    def has_reaction_event(self, message_id):
        """Есть ли уже сбор реакций за это сообщение бота (в любом состоянии)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM bump_claim_events WHERE source_message_id = ?",
                (message_id,),
            ).fetchone()
        return row is not None

    def should_start_reaction_event(self, message_id, chance_percent):
        """Один раз определяет шанс запуска для подтверждения успешного бампа.

        Решение сохраняется в БД: повторные правки сообщения не бросают шанс заново.
        """
        chance_percent = max(0, min(100, int(chance_percent)))
        with self._connect() as conn:
            # IMMEDIATE сериализует проверку и запись, чтобы параллельные обработчики
            # одного сообщения не могли провести два независимых броска.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT should_start FROM bump_reaction_event_rolls WHERE source_message_id = ?",
                (message_id,),
            ).fetchone()
            if row is not None:
                return bool(row[0])

            should_start = random.random() < chance_percent / 100
            conn.execute(
                """INSERT INTO bump_reaction_event_rolls
                   (source_message_id, should_start) VALUES (?, ?)""",
                (message_id, int(should_start)),
            )
            return should_start

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

        У Carl-bot и Discodus награда прямая только если Discord сам указал, кто вызвал
        команду (interaction_metadata); иначе для них запускается сбор реакций.
        """
        if not message.guild or not self.enabled_in(message.guild.id):
            return None
        if not message.author.bot or message.author.id not in BUMP_BOT_IDS:
            return None
        reaction_bot = message.author.id in REACTION_BUMP_BOT_IDS
        if reaction_bot and not DIRECT_REWARD_FOR_REACTION_BOTS:
            return None
        if not self._is_success_message(message):
            return None

        user_id, source = self.find_initiator(message)
        if reaction_bot:
            # Только достоверный инициатор; а если сбор реакций за это сообщение уже
            # идёт, второй путь награждения не нужен.
            if source not in RELIABLE_INITIATOR_SOURCES or self.has_reaction_event(message.id):
                return None
        elif user_id is None:
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
