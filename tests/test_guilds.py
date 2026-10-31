"""Ограничение серверов: ALLOWED_GUILD_IDS и выход бота с чужих серверов."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from config import var
from src.bot.handlers.guilds import GuildHandlers
from src.utils.utils import utils


@pytest.fixture(autouse=True)
def only_guilds_1_and_2(monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [1, 2])


def fake_guild(guild_id, leave=None):
    return SimpleNamespace(id=guild_id, name=f"Сервер {guild_id}", leave=leave or AsyncMock())


def test_is_guild_allowed(monkeypatch):
    assert utils.is_guild_allowed(1) is True
    assert utils.is_guild_allowed(3) is False
    assert utils.is_guild_allowed(None) is True            # личные сообщения разрешены
    monkeypatch.setattr(var, "allowed_guild_ids", [])
    assert utils.is_guild_allowed(3) is True               # пустой список = без ограничений


def test_unknown_guild_is_left_immediately():
    guild = fake_guild(3)
    asyncio.run(GuildHandlers().on_join(guild))
    guild.leave.assert_awaited_once()


def test_allowed_guild_is_kept():
    guild = fake_guild(1)
    asyncio.run(GuildHandlers().on_join(guild))
    guild.leave.assert_not_called()


def test_startup_sweep_leaves_only_unknown_guilds():
    guilds = [fake_guild(1), fake_guild(3), fake_guild(2), fake_guild(4)]
    asyncio.run(GuildHandlers().sweep(SimpleNamespace(guilds=guilds)))
    left = [g.id for g in guilds if g.leave.await_count]
    assert left == [3, 4]


def test_sweep_does_nothing_when_list_is_empty(monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [])
    guilds = [fake_guild(3)]
    asyncio.run(GuildHandlers().sweep(SimpleNamespace(guilds=guilds)))
    guilds[0].leave.assert_not_called()


def test_failed_leave_does_not_crash():
    response = SimpleNamespace(status=500, reason="Server Error")
    guild = fake_guild(3, leave=AsyncMock(side_effect=discord.HTTPException(response, "ошибка")))
    assert asyncio.run(GuildHandlers().leave_if_not_allowed(guild)) is True
