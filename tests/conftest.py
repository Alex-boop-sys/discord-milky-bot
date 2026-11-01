"""Общие настройки тестов. Файл читается pytest-ом автоматически, до всех тестов.

Что он делает:
  1. Задаёт тестовые ID серверов и каналов, чтобы результат тестов не зависел от
     вашего настоящего .env (значения из окружения важнее значений из файла .env).
  2. Подменяет пути к базам на временные: тесты НИКОГДА не трогают настоящие
     data/history.db и data/economy.db бота.
"""
import os
import tempfile

# Важно: это нужно сделать ДО импорта config.
os.environ["MAIN_GUILD_ID"] = "1000"
os.environ["ALLOWED_GUILD_IDS"] = "1000"
os.environ["ECONOMY_GUILD_IDS"] = "1000"
os.environ["GREETINGS_CHANNEL_IDS"] = "2000"
os.environ["OWNER_ID"] = ""
os.environ["CO_OWNER_ID"] = ""

import config  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="milky_tests_")
config.paths.db_path = os.path.join(_TMP, "history.db")
config.paths.economy_db = os.path.join(_TMP, "economy.db")
config.paths.morning_greetings_state = os.path.join(_TMP, "morning_state.json")
config.paths.ignored_users = os.path.join(_TMP, "ignored_users.json")


import pytest  # noqa: E402


@pytest.fixture
def world(monkeypatch):
    """Два сервера, три канала и два сообщения; владелец бота - пользователь OWNER."""
    from tests.admin_helpers import OWNER, World

    monkeypatch.setattr(config.env, "owner_ids", [OWNER])
    return World()
