"""Админ-команды root# inspect и root# sniff."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from src.bot import admin_commands as admin_module
from src.bot.admin_commands import USAGE
from src.features.bump_sniffer import BumpSniffer
from tests.admin_helpers import C1, G1, M1, http_error, marks, replied, run
from tests.real_messages import BUMPER, DISBOARD, LOADING, embed, make_real_message

SUCCESS = "You've successfully bumped this server"


def sent_json(message):
    """Содержимое JSON-файла, который бот приложил к ответу."""
    return json.loads(message.reply.await_args.kwargs["file"].fp.read().decode("utf-8"))


@pytest.fixture
def sniffer(monkeypatch):
    fresh = BumpSniffer()
    monkeypatch.setattr(admin_module, "bump_sniffer", fresh)
    return fresh


def with_message(world, real_message, raw=None, raw_error=None):
    """Канал C1 «отдаёт» настоящее сообщение; client.http отдаёт исходные данные."""
    world.c1.fetch_message = AsyncMock(return_value=real_message)
    get_message = AsyncMock(side_effect=raw_error) if raw_error else AsyncMock(return_value=raw)
    world.client.http = SimpleNamespace(get_message=get_message)
    return get_message


# --------------------------------------------------------------------- inspect

def test_inspect_shows_the_verdict_and_attaches_full_dump(world):
    real = make_real_message(content=SUCCESS)
    get_message = with_message(world, real, raw={"id": str(real.id), "flags": 0})
    _, message = run(f"root# inspect {C1} {real.id}", world)

    text = replied(message)[0]
    assert f"сообщение `{real.id}`" in text and "Carl-bot" in text
    assert "✅ Есть фраза успешного бампа" in text and f"{BUMPER} (источник: interaction_metadata)" in text
    assert "**Итог:** начислит награду" in text
    get_message.assert_awaited_once_with(C1, real.id)

    kwargs = message.reply.await_args.kwargs
    assert kwargs["file"].filename == f"inspect-{real.id}.json"
    assert kwargs["delete_after"] == 300 and kwargs["allowed_mentions"].users is False
    dump = sent_json(message)
    assert dump["raw"] == {"id": str(real.id), "flags": 0} and dump["diagnosis"]["verdict"] == "reward"


@pytest.mark.parametrize("target", [lambda r: f"{G1} {C1} {r.id}", lambda r: f"https://discord.com/channels/{G1}/{C1}/{r.id}", lambda r: f"<#{C1}> {r.id}"])
def test_inspect_accepts_ids_mention_and_link(world, target):
    real = make_real_message(content=SUCCESS)
    with_message(world, real)
    _, message = run(f"root# inspect {target(real)}", world)
    assert f"сообщение `{real.id}`" in replied(message)[0]


def test_inspect_explains_why_a_message_is_ignored(world):
    real = make_real_message(bot_id=123456789012345678, bot_name="Неизвестный бот", content=SUCCESS)
    with_message(world, real)
    _, message = run(f"root# inspect {C1} {real.id}", world)
    text = replied(message)[0]
    assert "❌ Бот известен экономике" in text and "нет в BUMP_BOT_IDS" in text
    assert "игнорирует: не выполнено «Бот известен экономике»" in text


def test_inspect_of_a_still_loading_message(world):
    real = make_real_message(initiator=None, flags=LOADING)
    with_message(world, real)
    _, message = run(f"root# inspect {C1} {real.id}", world)
    assert "❌ Сообщение готово" in replied(message)[0] and "правкой" in replied(message)[0]
    assert sent_json(message)["flags"] == ["loading"]


def test_inspect_works_when_raw_data_cannot_be_fetched(world):
    real = make_real_message(content=SUCCESS)
    with_message(world, real, raw_error=http_error(discord.Forbidden, 403))
    _, message = run(f"root# inspect {C1} {real.id}", world)
    assert marks(message) == [] and "raw" not in sent_json(message)       # отчёт всё равно пришёл


def test_inspect_works_with_embed_text(world):
    real = make_real_message(bot_id=DISBOARD, bot_name="DISBOARD", embeds=[embed(description="Bump done! :thumbsup:")])
    with_message(world, real)
    _, message = run(f"root# inspect {C1} {real.id}", world)
    assert "найдена «bump done»" in replied(message)[0]


def test_inspect_escapes_markdown_in_bot_name(world):
    real = make_real_message(bot_name="*Жирный* бот", content=SUCCESS)
    with_message(world, real)
    _, message = run(f"root# inspect {C1} {real.id}", world)
    assert "\\*Жирный\\* бот" in replied(message)[0]


@pytest.mark.parametrize("command", ["inspect", "inspect привет", f"inspect {M1} лишнее"])
def test_inspect_wrong_format(world, command):
    _, message = run(f"root# {command}", world)
    assert USAGE["inspect"] in replied(message)[0] and marks(message) == ["❌"]


def test_inspect_of_missing_message(world):
    world.c1.fetch_message = AsyncMock(side_effect=http_error(discord.NotFound, 404))
    _, message = run(f"root# inspect {C1} 888888888888888888", world)
    assert marks(message) == ["❌"] and replied(message) == []


# ----------------------------------------------------------------------- sniff

def test_sniff_on_defaults_to_thirty_minutes(world, sniffer):
    _, message = run("root# sniff on", world)
    assert sniffer.is_active() and 1790 < sniffer.remaining() <= 1800
    assert "30 мин" in replied(message)[0] and "root# sniff show" in replied(message)[0]


@pytest.mark.parametrize("minutes", [1, 10, 240])
def test_sniff_on_with_minutes(world, sniffer, minutes):
    run(f"root# sniff on {minutes}", world)
    assert minutes * 60 - 10 < sniffer.remaining() <= minutes * 60


@pytest.mark.parametrize(
    "command",
    ["sniff on 0", "sniff on 241", "sniff on abc", "sniff on 5 6", "sniff on -3", "sniff foo", "sniff off 5", "sniff show x", "sniff clear 1"],
)
def test_sniff_wrong_format(world, sniffer, command):
    _, message = run(f"root# {command}", world)
    assert USAGE["sniff"] in replied(message)[0] and marks(message) == ["❌"]
    assert not sniffer.is_active()


def test_sniff_status_off_and_on(world, sniffer):
    _, message = run("root# sniff", world)                      # без параметров - состояние
    assert "выключена" in replied(message)[0] and "записано: 0" in replied(message)[0]

    sniffer.start(60)
    _, message = run("root# sniff status", world)
    assert "включена, осталось 59 мин" in replied(message)[0] or "включена, осталось 1 ч" in replied(message)[0]


def test_sniff_off_and_clear(world, sniffer):
    sniffer.start(5)
    sniffer.record("create", make_real_message(content=SUCCESS))
    _, message = run("root# sniff off", world)
    assert not sniffer.is_active() and "записано: 1" in replied(message)[0]

    _, message = run("root# sniff clear", world)
    assert sniffer.count() == 0 and marks(message) == ["✅"]


def test_sniff_show_without_records_explains_what_it_means(world, sniffer):
    _, message = run("root# sniff show", world)
    assert "Записей пока нет" in replied(message)[0] and "не видит сообщений" in replied(message)[0]
    assert "file" not in message.reply.await_args.kwargs


def test_sniff_show_sends_summary_and_file(world, sniffer):
    sniffer.start(5)
    sniffer.record("create", make_real_message(flags=LOADING, initiator=None))
    sniffer.record("edit", make_real_message(content=SUCCESS, initiator=None), raw={"id": "1"})
    _, message = run("root# sniff show", world)

    text = replied(message)[0]
    assert "Записано сообщений ботов: 2" in text and "Carl-bot" in text and "сбор реакций" in text
    kwargs = message.reply.await_args.kwargs
    assert kwargs["file"].filename == "sniffer.json" and kwargs["delete_after"] == 300
    records = sent_json(message)
    assert [r["event"] for r in records] == ["create", "edit"] and records[1]["raw"] == {"id": "1"}


@pytest.mark.parametrize("command", ["inspect 1", "sniff on", "sniff show"])
def test_diagnostics_are_owner_only(world, sniffer, command):
    _, message = run(f"root# {command}", world, author_id=123)
    assert replied(message) == ["⛔ Эта административная команда тебе недоступна."]
    assert not sniffer.is_active()
