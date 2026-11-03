"""Путь бампа через обработчики: новое сообщение, правка сообщения, награда или сбор реакций."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.bot.handlers import economy as events_module
from src.bot.handlers import messages as messages_module
from src.bot.handlers.economy import EconomyEventHandler
from src.bot.handlers.messages import MessageHandlers
from src.features.bump_sniffer import BumpSniffer
from tests.real_messages import BUMPER, DISBOARD, LOADING, make_real_message, next_id

OWN_ID = 999
SUCCESS = "You've successfully bumped this server"


@pytest.fixture
def flow(eco, monkeypatch):
    """Обработчики на временной базе; сбор реакций - настоящий, но без фоновой задачи."""
    monkeypatch.setattr(messages_module, "economy", eco)
    monkeypatch.setattr(events_module, "economy", eco)
    # В тестах по умолчанию шанс считаем успешным, чтобы тестировать сам запуск.
    monkeypatch.setattr(eco, "should_start_reaction_event", lambda _id, _chance: True)
    events = EconomyEventHandler()
    monkeypatch.setattr(events, "_ensure_reaction_worker", Mock())
    handlers = MessageHandlers()
    handlers.economy_events = events
    handlers.client = SimpleNamespace(user=SimpleNamespace(id=OWN_ID))
    return SimpleNamespace(handlers=handlers, events=events, eco=eco)


def created(flow, message):
    asyncio.run(flow.handlers.on_msg(message, SimpleNamespace(id=OWN_ID)))


def edited(flow, message):
    asyncio.run(flow.handlers.on_raw_message_edit(SimpleNamespace(message=message, data={"id": str(message.id)})))


def queued(flow):
    return flow.events.reaction_event_queue.qsize()


# ------------------------------------- бот сначала «думает», итог приходит правкой

def test_deferred_reply_with_known_initiator_is_rewarded_on_edit(flow):
    message_id = next_id()
    created(flow, make_real_message(message_id=message_id, flags=LOADING))        # «Carl-bot думает…»
    assert flow.eco.get_balance(BUMPER) == 0 and queued(flow) == 0                 # пока ничего

    final = make_real_message(message_id=message_id, content=SUCCESS)
    edited(flow, final)
    assert flow.eco.get_balance(BUMPER) == 1
    text = final.channel.send.await_args.args[0]
    assert f"<@{BUMPER}>" in text and "Геофрукт" in text                           # награда объявлена

    edited(flow, make_real_message(message_id=message_id, content=SUCCESS))        # ещё одна правка
    assert flow.eco.get_balance(BUMPER) == 1                                       # награды не удваиваются
    assert queued(flow) == 0                                                       # и мини-игры нет


def test_deferred_reply_with_unknown_initiator_starts_one_minigame(flow):
    message_id = next_id()
    created(flow, make_real_message(message_id=message_id, initiator=None, flags=LOADING))
    assert queued(flow) == 0

    for _ in range(3):                                                             # несколько правок подряд
        edited(flow, make_real_message(message_id=message_id, initiator=None, content=SUCCESS))
    assert queued(flow) == 1 and flow.events.queued_event_ids == {message_id}
    assert flow.eco.has_reaction_event(message_id) and flow.eco.get_balance(BUMPER) == 0


# ------------------------------------------------------- ответ сразу, без правок

def test_immediate_reply_with_unknown_initiator_starts_minigame_once(flow):
    message_id = next_id()
    created(flow, make_real_message(message_id=message_id, initiator=None, content=SUCCESS))
    edited(flow, make_real_message(message_id=message_id, initiator=None, content=SUCCESS))
    assert queued(flow) == 1


def test_immediate_reply_with_known_initiator_gets_reward_and_may_start_minigame(flow):
    message_id = next_id()
    created(flow, make_real_message(message_id=message_id, content=SUCCESS))
    edited(flow, make_real_message(message_id=message_id, content=SUCCESS))        # потом ещё и правка
    assert flow.eco.get_balance(BUMPER) == 1 and queued(flow) == 1
    assert flow.eco.has_reaction_event(message_id)                                 # мини-ивент дополнительно


def test_other_bump_bots_work_as_before(flow):
    message = make_real_message(bot_id=DISBOARD, bot_name="DISBOARD", content="Bump done!")
    created(flow, message)
    assert flow.eco.get_balance(BUMPER) == 1 and message.channel.send.await_count == 1
    assert queued(flow) == 0                                                       # другой бот не запускает мини-ивент


# ---------------------------------------------------------- что игнорируется

@pytest.mark.parametrize(
    "kwargs",
    [
        dict(is_bot=False),                                   # правка сообщения человека
        dict(bot_id=OWN_ID, bot_name="Милки"),                # собственные правки (обратный отсчёт раздачи)
        dict(guild_id=777),                                   # сервер, где бот не работает
        dict(bot_id=123456789012345678),                      # бот, которого экономика не знает
        dict(content="Time to bump!"),                        # не успешный бамп
    ],
)
def test_edits_that_must_be_ignored(flow, kwargs):
    kwargs.setdefault("content", SUCCESS)
    message = make_real_message(**kwargs)
    edited(flow, message)
    assert flow.eco.get_balance(BUMPER) == 0 and queued(flow) == 0
    message.channel.send.assert_not_called()


# ------------------------------------------------------ запись для диагностики

def test_sniffer_records_creates_and_edits_of_bots_only(flow, monkeypatch):
    sniffer = BumpSniffer()
    monkeypatch.setattr(messages_module, "bump_sniffer", sniffer)
    sniffer.start(10)

    created(flow, make_real_message(flags=LOADING))
    final = make_real_message(content=SUCCESS)
    edited(flow, final)
    created(flow, make_real_message(is_bot=False, content="привет"))              # человек
    created(flow, make_real_message(bot_id=OWN_ID, content="моё сообщение"))      # сам Милки
    edited(flow, make_real_message(bot_id=OWN_ID, content="моя правка"))

    assert [r["event"] for r in sniffer.snapshot()] == ["create", "edit"]
    assert sniffer.snapshot()[1]["raw"] == {"id": str(final.id)}                  # у правки сохранены исходные данные


def test_sniffer_is_silent_when_switched_off(flow, monkeypatch):
    sniffer = BumpSniffer()
    monkeypatch.setattr(messages_module, "bump_sniffer", sniffer)
    created(flow, make_real_message(content=SUCCESS))
    edited(flow, make_real_message(content=SUCCESS))
    assert sniffer.count() == 0


def test_economy_is_asked_only_about_edits_of_other_bots(flow, monkeypatch):
    """Правки людей и собственные правки Милки отсекаются сразу, не доходя до экономики."""
    handle = AsyncMock()
    monkeypatch.setattr(MessageHandlers, "handle_economy", handle)

    edited(flow, make_real_message(is_bot=False, content=SUCCESS))
    edited(flow, make_real_message(bot_id=OWN_ID, bot_name="Милки", content=SUCCESS))
    handle.assert_not_called()

    other_bot = make_real_message(content=SUCCESS)
    edited(flow, other_bot)
    handle.assert_awaited_once_with(other_bot)



def test_known_initiator_minigame_chance_is_decided_only_once(flow, monkeypatch):
    message_id = next_id()
    decisions = []
    monkeypatch.setattr(
        flow.eco,
        "should_start_reaction_event",
        lambda source_id, chance: decisions.append((source_id, chance)) or False,
    )

    message = make_real_message(message_id=message_id, content=SUCCESS)
    created(flow, message)
    edited(flow, make_real_message(message_id=message_id, content=SUCCESS))

    assert flow.eco.get_balance(BUMPER) == 1
    assert queued(flow) == 0
    assert decisions == [(message_id, 50)]                                        # только один бросок
