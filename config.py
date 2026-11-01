# config.py
import os
import shutil
from pathlib import Path

import discord
from dotenv import load_dotenv

# .env хранится в корне проекта, рядом с main.py.
env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

# Версия используется административной командой root# ver.
VERSION = "0.6.0 [INDEV]"


# ENV ПЕРЕМЕННЫЕ
class Env:
    def __init__(self):
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.discord_token = os.getenv("DISCORD_TOKEN")
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.cloudflare_account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        self.zai_api_key = os.getenv("ZAI_API_KEY")
        self.owner_id = os.getenv("OWNER_ID")
        self.co_owner_id = os.getenv("CO_OWNER_ID")
        # Эти значения необязательны: список содержит только корректные числовые ID.
        self.owner_ids = []
        for owner_id in (self.owner_id, self.co_owner_id):
            if owner_id and owner_id.strip().isdigit():
                parsed_id = int(owner_id.strip())
                if parsed_id not in self.owner_ids:
                    self.owner_ids.append(parsed_id)
        self._cache = {}

    def __iter__(self):
        for attr_name in dir(self):
            if not attr_name.startswith("_") and not callable(getattr(self, attr_name)):
                yield attr_name, getattr(self, attr_name)


env = Env()


# ПУТИ
class Paths:
    def __init__(self):
        self.root = os.path.dirname(os.path.abspath(__file__))
        self.src = os.path.join(self.root, "src")
        self.resources = os.path.join(self.root, "resources")
        self.data = os.path.join(self.root, "data")
        self.logs = os.path.join(self.root, "logs")
        self.dicts = os.path.join(self.resources, "dicts.json")

        # Базы и состояние отделены от ресурсов приложения.
        self.db_path = os.path.join(self.data, "history.db")
        self.economy_db = os.path.join(self.data, "economy.db")
        self.morning_greetings_state = os.path.join(
            self.data, "morning_greetings_state.json"
        )
        # Список игнорируемых пользователей (ID, ники, причины) - личные данные,
        # поэтому он лежит в data/ и в git не попадает. Образец: resources/*.example.json
        self.ignored_users = os.path.join(self.data, "ignored_users.json")

        os.makedirs(self.resources, exist_ok=True)
        os.makedirs(self.data, exist_ok=True)
        os.makedirs(self.logs, exist_ok=True)

        # Безопасная миграция старых данных: копируем, а не удаляем исходники.
        # Если файл в data/ уже есть, он считается актуальным и не перезаписывается.
        legacy_files = {
            os.path.join(self.resources, "history.db"): self.db_path,
            os.path.join(self.resources, "economy.db"): self.economy_db,
            os.path.join(
                self.resources, "morning_greetings_state.json"
            ): self.morning_greetings_state,
            os.path.join(self.resources, "ignored_users.json"): self.ignored_users,
        }
        for old_path, new_path in legacy_files.items():
            if os.path.isfile(old_path) and not os.path.exists(new_path):
                shutil.copy2(old_path, new_path)


paths = Paths()


def _env_int(name):
    """Целое число (ID) из .env. Если переменная пустая или не задана - 0.

    Опечатка в значении - ошибка при запуске с понятным текстом, а не тихий пропуск.
    """
    value = (os.getenv(name) or "").strip()
    if not value:
        return 0
    if not value.isdigit():
        raise ValueError(f"{name} в .env должен быть числом (ID), а там: {value!r}")
    return int(value)


def _env_int_list(name, default=()):
    """Список ID из .env через запятую (например 111,222).

    Если переменная пустая или не задана - берётся default. Опечатка в значении -
    ошибка при запуске: иначе, например, бот мог бы по ошибке покинуть сервер.
    """
    ids = []
    for part in (os.getenv(name) or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(
                f"{name} в .env: ID через запятую, цифрами, а там: {part!r}"
            )
        ids.append(int(part))
    return ids or list(default)


# ПЕРЕМЕННЫЕ (ТОЛЬКО НАСТРОЙКИ, БЕЗ СОЗДАНИЯ БОТА!)
class Var:
    def __init__(self):
        self.fallback_models = [
            ("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
            ("zai", "glm-4.5-flash"),
            ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
            ("openrouter", "openrouter/free"),
            ("groq", "llama-3.3-70b-versatile"),
        ]

        # НАСТРОЙКИ INTENTS
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.intents.members = True
        # Presence Intent выключен: он нужен только командам /шип и /обнять
        # (сейчас они отключены). Если вернёте их, включите здесь
        # self.intents.presences = True и Presence Intent на портале разработчиков Discord.

        self.bot_ver = VERSION

        # --- СЕРВЕРЫ И КАНАЛЫ -------------------------------------------------
        # Все ID хранятся ТОЛЬКО в .env (см. .env.example), в коде их нет.
        # Как узнать ID: Discord -> Настройки -> Расширенные -> «Режим разработчика»,
        # затем ПКМ по серверу или каналу -> «Копировать ID».

        # Основной сервер: под его ID хранится общий баланс Геофруктов.
        # 0 = не задан. Если экономика уже работала, значение должно остаться прежним.
        self.main_guild_id = _env_int("MAIN_GUILD_ID")
        main_guild = [self.main_guild_id] if self.main_guild_id else []

        # Серверы, где бот работает; с любого другого он сразу выходит. Если список
        # не задан, разрешён только основной сервер; если нет и его - ограничений нет.
        self.allowed_guild_ids = _env_int_list("ALLOWED_GUILD_IDS", main_guild)

        # Серверы с экономикой Геофруктов (баланс общий для всех). По умолчанию -
        # только основной сервер.
        self.economy_guild_ids = _env_int_list("ECONOMY_GUILD_IDS", main_guild)

        # Каналы утреннего приветствия (везде один и тот же текст). Пусто - приветствия
        # не отправляются.
        self.morning_greetings_channel_ids = _env_int_list("GREETINGS_CHANNEL_IDS")

        # УТРЕННИЕ ПРИВЕТСТВИЯ МИЛКИ (время задаётся в UTC)
        self.morning_greetings_enabled = True
        self.morning_greetings_start_hour_utc = 4
        self.morning_greetings_end_hour_utc = 5
        self.morning_greetings_attempts = 3
        self.morning_greetings_retry_delay = 60
        # Одна попытка генерации ограничена по времени, чтобы повторы не затянулись.
        self.morning_greetings_request_timeout = 50

        # Таймер: не чаще одного обращения к боту (общение, арты, генерация) за
        # столько секунд от одного человека. 0 - таймер выключен.
        self.user_cooldown_seconds = 5

        # Обращение игнорируется, если текст запроса начинается с одного из этих знаков
        # и сразу за ним идёт буква или цифра: «!play», «.r d20», «/help», «-skip».
        # Так бот не отвечает на команды других ботов. Обычный текст не страдает:
        # «Милки: привет», «Милки - привет», «...ну ладно», «:)» отвечаются как обычно.
        # Обратная косая черта "\\" в начале игнорируется всегда: это способ сказать
        # «не отвечай», например, отвечая на сообщение бота.
        self.ignored_prefixes = ("!", ".", "/", "-", ";", ":", "\\")

        self.max_history = 10
        self.cloudflare_timeout = 25
        self.groq_timeout = 25
        self.openrouter_timeout = 25
        self.zai_timeout = 30

        # настройки ответов нейронок
        self.temperature = 0.85
        self.max_tokens = 320
        self.top_p = 0.9

        # объём картинки в дискорд, завиясщие от текущего буста
        self.limits = {
            0: 8 * 1024 * 1024,
            1: 8 * 1024 * 1024,
            2: 25 * 1024 * 1024,
            3: 50 * 1024 * 1024,
        }


var = Var()
