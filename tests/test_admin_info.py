"""Админ-команды root# servers, status, ai и вспомогательные функции форматирования."""
import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import paths, var
from src.bot import admin_commands as admin_module
from src.bot.admin_commands import fit_lines, format_duration, format_size
from src.services.ai import ProviderCheck
from src.services.ignore_list import IgnoreList
from tests.admin_helpers import G1, G2, OWNER, marks, replied, run
from tests.helpers import make_message


# ------------------------------------------------------------ форматирование

@pytest.mark.parametrize(
    "seconds, text",
    [(0, "0 с"), (59, "59 с"), (60, "1 мин"), (120, "2 мин"), (3661, "1 ч 1 мин"), (3600, "1 ч"),
     (86400, "1 д"), (90000, "1 д 1 ч"), (200000, "2 д 7 ч 33 мин"), (59.9, "59 с")],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    "size, text", [(0, "0 Б"), (1023, "1023 Б"), (1024, "1 КБ"), (49152, "48 КБ"), (1572864, "1.5 МБ")]
)
def test_format_size(size, text):
    assert format_size(size) == text


def test_fit_lines_cuts_to_discord_limit_and_counts_the_rest():
    lines = [f"строка {i} " + "я" * 90 for i in range(100)]
    text = fit_lines("**Заголовок**\n", lines)
    assert len(text) <= 2000 and text.startswith("**Заголовок**\nстрока 0")
    shown = text.count("строка ")
    assert text.endswith(f"…и ещё {100 - shown}")


def test_fit_lines_keeps_everything_when_it_fits():
    assert fit_lines("шапка\n", ["a", "b"]) == "шапка\na\nb"


# -------------------------------------------------- права на новые команды

@pytest.mark.parametrize("command", ["ban 888888888888888801", "unban 888888888888888801", "bans", "servers", "status", "ai"])
def test_non_owner_cannot_use_new_commands(world, command):
    _, message = run(f"root# {command}", world, author_id=123)
    assert replied(message) == ["⛔ Эта административная команда тебе недоступна."]


# ---------------------------------------------------------------------- servers

@pytest.fixture
def guilds(world, monkeypatch):
    def guild(guild_id, name, members):
        return SimpleNamespace(id=guild_id, name=name, member_count=members)

    world.client.guilds = [guild(G2, "яблоко", 5), guild(G1, "Арбуз", 120), guild(333333333333333300, "Без числа", None)]
    monkeypatch.setattr(var, "main_guild_id", G1)
    monkeypatch.setattr(var, "economy_guild_ids", [G1, G2])
    monkeypatch.setattr(var, "allowed_guild_ids", [G1])
    return world


def test_servers_lists_all_guilds_with_markers(guilds):
    _, message = run("root# servers", guilds)
    lines = replied(message)[0].split("\n")

    assert "Серверы Милки (3)" in lines[0]
    body = lines[2:]
    assert [line.split("**")[1] for line in body] == ["Арбуз", "Без числа", "яблоко"]   # по алфавиту без учёта регистра
    assert f"(`{G1}`) — 120 уч. 🏠 🫐" in body[0]                    # основной и с экономикой, разрешён
    assert "— ? уч. ⚠️" in body[1]                                    # число участников неизвестно, нет в списке
    assert f"(`{G2}`) — 5 уч. 🫐 ⚠️" in body[2]                        # экономика есть, но сервер не разрешён
    assert message.reply.await_args.kwargs["delete_after"] == 60
    assert message.reply.await_args.kwargs["allowed_mentions"].users is False
    assert marks(message) == []


def test_servers_without_restriction_has_no_warnings(guilds, monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [])
    _, message = run("root# servers", guilds)
    assert "⚠️" not in "".join(replied(message)[0].split("\n")[2:])


def test_servers_with_many_guilds_fits_into_message(world, monkeypatch):
    world.client.guilds = [SimpleNamespace(id=500000000000000000 + i, name=f"Сервер {i} " + "я" * 40, member_count=i) for i in range(200)]
    _, message = run("root# servers", world)
    text = replied(message)[0]
    assert len(text) <= 2000 and "…и ещё " in text


def test_servers_without_client(world):
    _, message = run("root# servers", world, client=None)
    assert marks(message) == ["❌"]


# ----------------------------------------------------------------------- status

@pytest.fixture
def status_world(world, monkeypatch, tmp_path):
    (tmp_path / "history.db").write_bytes(b"x" * 49152)
    monkeypatch.setattr(paths, "db_path", str(tmp_path / "history.db"))
    monkeypatch.setattr(paths, "economy_db", str(tmp_path / "нет-такого.db"))
    monkeypatch.setattr(admin_module, "STARTED_AT", time.monotonic() - 3700)
    monkeypatch.setattr(admin_module, "ignore_list", IgnoreList(str(tmp_path / "ignored.json")))
    monkeypatch.setattr(admin_module, "history", SimpleNamespace(
        get_summary=lambda: {"history_rows": 120, "users": 35, "total_messages": 1840}))
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(var, "max_history", 10)
    monkeypatch.setattr(var, "morning_greetings_channel_ids", [1, 2])
    monkeypatch.setattr(var, "economy_guild_ids", [1])
    world.client.latency = 0.0853
    world.client.guilds = [SimpleNamespace(id=G1, name="А", member_count=100), SimpleNamespace(id=G2, name="Б", member_count=12)]
    return world


def test_status_report(status_world):
    admin_module.ignore_list.add(1, "Алекс")
    _, message = run("root# status", status_world)
    text = replied(message)[0]

    assert admin_module.VERSION in text
    assert "Работает: 1 ч 1 мин" in text and "задержка до Discord: 85 мс" in text
    assert "Серверов: 2 (участников: 112)" in text
    assert "История: 120 записей · статистика: 35 чел., 1840 обращений" in text
    assert "history.db 48 КБ" in text and "economy.db нет файла" in text
    assert "Игнорируется: 1" in text and "таймер: 5 с" in text and "контекст: 10 записей" in text
    assert "Приветствия: каналов 2" in text and "экономика: серверов 1" in text
    assert message.reply.await_args.kwargs["delete_after"] == 60 and marks(message) == []


@pytest.mark.parametrize("latency", [math.nan, math.inf])
def test_status_when_latency_is_unknown(status_world, latency):
    status_world.client.latency = latency
    _, message = run("root# status", status_world)
    assert "задержка до Discord: —" in replied(message)[0]


def test_status_works_without_client(status_world):
    _, message = run("root# status", status_world, client=None)
    assert "Серверов: 0" in replied(message)[0] and "задержка до Discord: —" in replied(message)[0]
    assert marks(message) == []


# --------------------------------------------------------------------------- ai

def set_check_results(monkeypatch, results):
    check = AsyncMock(return_value=results)
    monkeypatch.setattr(admin_module, "api", SimpleNamespace(check_providers=check))
    return check


def test_ai_report_explains_every_state(world, monkeypatch):
    monkeypatch.setattr(var, "cloudflare_timeout", 25)
    check = set_check_results(monkeypatch, [
        ProviderCheck("cloudflare", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "ok", 1.234),
        ProviderCheck("zai", "glm-4.5-flash", "no_key", 0.0),
        ProviderCheck("groq", "llama-3.3-70b-versatile", "fail", 3.2),
        ProviderCheck("openrouter", "meta-llama/llama-3.3-70b-instruct", "fail", 25.0),
    ])
    monkeypatch.setattr(var, "groq_timeout", 30)
    monkeypatch.setattr(var, "openrouter_timeout", 25)

    _, message = run("root# ai", world)
    check.assert_awaited_once()
    lines = replied(message)[0].split("\n")

    assert "отвечают: 1 из 4" in lines[0]
    body = [line for line in lines if line.startswith("•")]
    assert body[0] == "• **cloudflare** · llama-3.3-70b-instruct-fp8-fast — ✅ 1.2 с"      # короткое имя модели
    assert "⚪ ключ не задан в .env" in body[1]
    assert "❌ нет ответа (причина в логе: `root# log`)" in body[2]       # 3.2 с из 30 - не таймаут
    assert "❌ таймаут (25 с)" in body[3]                                                    # ждали весь срок
    assert message.reply.await_args.kwargs["delete_after"] == 60


def test_ai_check_runs_under_typing_indicator(world, monkeypatch):
    set_check_results(monkeypatch, [])
    events = []
    message = make_message("root# ai", author_id=OWNER)

    class Typing:
        async def __aenter__(self):
            events.append("typing-start")

        async def __aexit__(self, *exc):
            events.append("typing-end")

    message.channel.typing = lambda: Typing()
    asyncio.run(admin_module.admin_commands.handle(message, "root# ai", world.client))
    assert events == ["typing-start", "typing-end"]
