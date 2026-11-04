"""Экономика: награды за бампы (на временной базе)."""
import sqlite3
from types import SimpleNamespace

from src.features import economy as economy_module

import pytest

from config import var
from src.features.economy import ECONOMY_KEY, Economy

DISBOARD = 302050872383242240    # называет инициатора сразу
CARL_BOT = 235148962103951360    # награда выдаётся через сбор реакций 🫐


def bump_message(message_id=1, author_id=DISBOARD, user_id=42, guild_id=ECONOMY_KEY, content="Bump done!"):
    """Подделка сообщения Discord: только те поля, которые читает код.

    user_id=None - как будто Discord не сообщает, кто вызвал команду.
    """
    return SimpleNamespace(
        id=message_id,
        content=content,
        embeds=[],
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=555),
        author=SimpleNamespace(id=author_id, bot=True),
        interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=user_id)) if user_id else None,
    )


@pytest.mark.parametrize(
    "count, word",
    [
        (0, "Геофруктов"), (1, "Геофрукт"), (2, "Геофрукта"), (4, "Геофрукта"),
        (5, "Геофруктов"), (11, "Геофруктов"), (12, "Геофруктов"), (14, "Геофруктов"),
        (21, "Геофрукт"), (22, "Геофрукта"), (101, "Геофрукт"), (111, "Геофруктов"),
    ],
)
def test_currency_word(count, word):
    assert Economy.currency_word(count) == word


def test_bump_is_rewarded_exactly_once(eco):
    msg = bump_message()
    assert eco.process_bump_message(msg) == 42
    assert eco.process_bump_message(msg) is None          # то же сообщение второй раз
    assert eco.get_balance(42) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"guild_id": 111},                                   # чужой сервер
        {"author_id": 999},                                  # не бамп-бот
        {"content": "Подождите перед следующим бампом"},     # не успешный бамп
        {"author_id": CARL_BOT, "user_id": None, "content": "You've successfully bumped this server"},  # инициатор неизвестен: мини-игра
    ],
)
def test_no_reward_when_conditions_not_met(eco, kwargs):
    assert eco.process_bump_message(bump_message(**kwargs)) is None
    assert eco.get_balance(42) == 0


def test_reaction_event_full_cycle(eco):
    msg = bump_message(author_id=CARL_BOT, user_id=None, content="You've successfully bumped this server")
    assert eco.is_reaction_bump_success(msg)

    assert eco.reserve_reaction_event(msg) is True
    assert eco.reserve_reaction_event(msg) is False       # одно подтверждение - одна раздача
    eco.activate_reaction_event(msg.id, 999)

    assert eco.add_reaction_participant(999, 7, ECONOMY_KEY, "🫐") is True
    assert eco.add_reaction_participant(999, 7, ECONOMY_KEY, "🫐") is False   # дважды нельзя
    assert eco.add_reaction_participant(999, 8, ECONOMY_KEY, "👍") is False   # не та реакция

    assert eco.finish_reaction_event(msg.id) == [7]
    assert eco.get_balance(7) == 1
    assert eco.finish_reaction_event(msg.id) is None      # повторное закрытие ничего не выдаёт
    assert eco.get_balance(7) == 1
    assert eco.get_top() == [{"user_id": 7, "balance": 1}]


def test_connections_are_closed_after_use(eco):
    with eco._connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):         # закрытое соединение
        conn.execute("SELECT 1")


def test_economy_works_only_on_listed_guilds(monkeypatch):
    monkeypatch.setattr(var, "economy_guild_ids", [ECONOMY_KEY, 777])
    assert Economy.enabled_in(ECONOMY_KEY) is True
    assert Economy.enabled_in(777) is True
    assert Economy.enabled_in(888) is False


def test_balance_is_shared_between_economy_guilds(eco, monkeypatch):
    monkeypatch.setattr(var, "economy_guild_ids", [ECONOMY_KEY, 777])
    assert eco.process_bump_message(bump_message(message_id=1, guild_id=ECONOMY_KEY)) == 42
    assert eco.process_bump_message(bump_message(message_id=2, guild_id=777)) == 42
    assert eco.get_balance(42) == 2                       # один общий баланс на оба сервера
    assert eco.get_top() == [{"user_id": 42, "balance": 2}]


def test_no_reward_on_server_without_economy(eco, monkeypatch):
    monkeypatch.setattr(var, "economy_guild_ids", [ECONOMY_KEY])
    assert eco.process_bump_message(bump_message(guild_id=777)) is None
    assert eco.get_balance(42) == 0



def test_reaction_event_chance_is_saved_once(eco, monkeypatch):
    calls = []
    monkeypatch.setattr(economy_module.random, "random", lambda: calls.append(True) or 0.9)

    assert eco.should_start_reaction_event(1234, 50) is False
    # Даже при повторном вызове с другим процентом возвращается исходное решение.
    assert eco.should_start_reaction_event(1234, 100) is False
    assert calls == [True]


@pytest.mark.parametrize("chance,random_value,expected", [(0, 0.0, False), (50, 0.49, True), (50, 0.5, False), (100, 0.99, True)])
def test_reaction_event_chance_boundaries(eco, monkeypatch, chance, random_value, expected):
    monkeypatch.setattr(economy_module.random, "random", lambda: random_value)
    assert eco.should_start_reaction_event(5000 + chance, chance) is expected


def test_refund_balance_restores_currency(eco):
    assert eco.process_bump_message(bump_message()) == 42
    assert eco.get_balance(42) == 1
    assert eco.refund_balance(42, 2) == 3
    assert eco.get_balance(42) == 3
