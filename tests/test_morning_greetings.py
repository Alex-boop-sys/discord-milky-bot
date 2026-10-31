"""Утренняя рассылка по нескольким каналам (без Discord, на подделках)."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from config import paths, var
from src.features.morning_greetings import MorningGreetings

TODAY = date(2026, 11, 2)


def fake_channel(channel_id, guild_id=1, send=None):
    guild = SimpleNamespace(id=guild_id, name=f"Сервер {guild_id}")
    return SimpleNamespace(id=channel_id, name=f"канал-{channel_id}", guild=guild, send=send or AsyncMock())


def fake_bot(*channels):
    by_id = {c.id: c for c in channels}
    return SimpleNamespace(
        get_channel=lambda cid: by_id.get(cid),
        fetch_channel=AsyncMock(side_effect=discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "нет")),
    )


@pytest.fixture
def mg(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "morning_greetings_state", str(tmp_path / "state.json"))
    monkeypatch.setattr(var, "allowed_guild_ids", [])      # ограничение серверов выключено
    monkeypatch.setattr(var, "morning_greetings_channel_ids", [10, 20, 30])
    return MorningGreetings()


def deliver(mg, bot, text="Доброе утро!"):
    return asyncio.run(mg._deliver(bot, text, TODAY))


def test_same_text_goes_to_all_channels(mg):
    channels = [fake_channel(10), fake_channel(20, guild_id=2), fake_channel(30, guild_id=3)]
    assert deliver(mg, fake_bot(*channels)) == 3
    for channel in channels:
        assert channel.send.await_count == 1
        assert channel.send.await_args.args[0] == "Доброе утро!"


def test_greeting_never_pings(mg):
    channel = fake_channel(10)
    deliver(mg, fake_bot(channel))
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.everyone is False and mentions.users is False and mentions.roles is False


def test_broken_channels_do_not_block_others(mg):
    forbidden = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "нет прав"))
    channels = [fake_channel(10, send=forbidden), fake_channel(30, guild_id=3)]   # канала 20 нет вовсе
    assert deliver(mg, fake_bot(*channels)) == 1
    assert channels[1].send.await_count == 1


def test_already_greeted_channels_are_skipped_after_restart(mg, tmp_path):
    channels = [fake_channel(10), fake_channel(20), fake_channel(30)]
    deliver(mg, fake_bot(*channels))

    restarted = MorningGreetings()                         # новый запуск бота читает файл состояния
    assert deliver(restarted, fake_bot(*channels)) == 0
    assert all(c.send.await_count == 1 for c in channels) # повторов нет


def test_only_failed_channel_is_retried(mg):
    flaky = AsyncMock(side_effect=[discord.HTTPException(SimpleNamespace(status=500, reason="Error"), "сбой"), None])
    ok = fake_channel(10)
    bad = fake_channel(20, send=flaky)
    bot = fake_bot(ok, bad)
    assert deliver(mg, bot) == 1                           # 10 отправлен, 20 упал
    assert deliver(mg, bot) == 1                           # повтор: только 20
    assert ok.send.await_count == 1 and bad.send.await_count == 2


def test_new_day_starts_over(mg):
    channel = fake_channel(10)
    bot = fake_bot(channel)
    asyncio.run(mg._deliver(bot, "вчера", date(2026, 11, 1)))
    asyncio.run(mg._deliver(bot, "сегодня", TODAY))
    assert channel.send.await_count == 2


def test_unknown_guild_is_skipped(mg, monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [1])
    allowed, stranger = fake_channel(10, guild_id=1), fake_channel(20, guild_id=99)
    assert deliver(mg, fake_bot(allowed, stranger)) == 1
    stranger.send.assert_not_called()


def test_scheduler_is_not_started_without_channels(mg, monkeypatch):
    # Без каналов планировщик не нужен: иначе бот каждое утро зря тратил бы запросы к ИИ.
    monkeypatch.setattr(var, "morning_greetings_enabled", True)
    monkeypatch.setattr(var, "morning_greetings_channel_ids", [])
    asyncio.run(mg.start(SimpleNamespace()))
    assert mg.task is None
