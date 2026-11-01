"""Админ-команды root# ban, unban и bans."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from config import env
from src.bot import admin_commands as admin_module
from src.bot.admin_commands import USAGE, split_duration, split_user
from src.services.ignore_list import IgnoreList, parse_time
from tests.admin_helpers import OWNER, http_error, marks, replied, run

U1, U2, U3 = 888888888888888801, 888888888888888802, 888888888888888803   # обычный человек, бот, другой человек
CO_OWNER = 999999999999999991


def make_user(user_id, name="alex", global_name="Алекс", bot=False):
    return SimpleNamespace(id=user_id, name=name, global_name=global_name, bot=bot)


@pytest.fixture
def ignores(monkeypatch, tmp_path):
    """Игнор-лист во временной папке, подставленный в админ-команды."""
    lst = IgnoreList(str(tmp_path / "ignored.json"))
    monkeypatch.setattr(admin_module, "ignore_list", lst)
    return lst


@pytest.fixture
def people(world, monkeypatch):
    """Люди, которых «знает» Discord: U1 в кэше, U3 - только через запрос к API."""
    monkeypatch.setattr(env, "owner_ids", [OWNER, CO_OWNER])
    cached = {U1: make_user(U1), U2: make_user(U2, "robot", None, bot=True)}
    fetched = {U3: make_user(U3, "dasha", None)}

    async def fetch_user(user_id):
        if user_id in fetched:
            return fetched[user_id]
        raise http_error(discord.NotFound, 404)

    world.client.get_user = lambda user_id: cached.get(user_id)
    world.client.fetch_user = AsyncMock(side_effect=fetch_user)
    return world


def entry_hours(entry):
    return (parse_time(entry["expires_at"]) - parse_time(entry["banned_at"])) / timedelta(hours=1)


# ---------------------------------------------------------------- разбор команд

@pytest.mark.parametrize(
    "args, expected",
    [
        (f"{U1} 7д спам", (U1, "7д спам")),
        (f"<@{U1}> 24", (U1, "24")),
        (f"<@!{U1}>", (U1, "")),
        (f"{U1}", (U1, "")),
        ("123", None),                                   # короткое число - не ID
        ("привет", None), ("", None),
        (f"{U1}текст", None),                            # ID вплотную к тексту
    ],
)
def test_split_user(args, expected):
    assert split_user(args) == expected


@pytest.mark.parametrize(
    "rest, expected",
    [
        ("", (None, "")),
        ("спам в чате", (None, "спам в чате")),
        ("24", (24, "")),
        ("24ч", (24, "")), ("24Ч", (24, "")), ("24h", (24, "")),
        ("7д", (168, "")), ("7Д", (168, "")), ("7d спам", (168, "спам")),
        ("3 раза предупреждали", (3, "раза предупреждали")),   # число в начале причины читается как срок
        ("24часа", (None, "24часа")),                      # «24часа» - не срок, а слово
    ],
)
def test_split_duration(rest, expected):
    assert split_duration(rest) == expected


@pytest.mark.parametrize("rest", ["0", "0д", "99999999 спам"])
def test_split_duration_rejects_zero_and_huge_values(rest):
    with pytest.raises(admin_module.UsageError):
        split_duration(rest)


# ------------------------------------------------------------------------- ban

def test_ban_without_term_is_permanent_and_remembers_everything(people, ignores):
    _, message = run(f"root# ban {U1} спам в чате", people)

    entry = ignores.get(U1)
    assert entry["nickname"] == "Алекс"                  # global_name, а не логин
    assert entry["reason"] == "спам в чате" and entry["banned_by"] == OWNER
    assert "expires_at" not in entry
    assert marks(message) == ["✅"]
    text = replied(message)[0]
    assert "бессрочно" in text and str(U1) in text and "Алекс" in text
    assert message.reply.await_args.kwargs["allowed_mentions"].users is False   # никого не пингуем


@pytest.mark.parametrize("term, hours", [("24", 24), ("24ч", 24), ("7д", 168), ("1d", 24)])
def test_ban_with_term(people, ignores, term, hours):
    _, message = run(f"root# ban {U1} {term} причина", people)
    assert entry_hours(ignores.get(U1)) == hours
    assert ignores.get(U1)["reason"] == "причина"
    assert "до " in replied(message)[0] and marks(message) == ["✅"]


def test_ban_by_mention_and_login_fallback(people, ignores):
    run(f"root# ban <@{U3}>", people)                    # нет global_name - берём логин; человек найден через API
    assert ignores.get(U3)["nickname"] == "dasha"
    people.client.fetch_user.assert_awaited_once_with(U3)


def test_ban_renews_existing_entry(people, ignores):
    run(f"root# ban {U1} 24 первый раз", people)
    run(f"root# ban {U1} второй раз", people)
    assert ignores.get(U1)["reason"] == "второй раз" and "expires_at" not in ignores.get(U1)
    assert len(ignores.active()) == 1


@pytest.mark.parametrize("command", ["ban", "ban привет", "ban 123", f"ban {U1} 0", f"ban {U1} 0д", f"ban {U1} 999999999"])
def test_ban_wrong_format_shows_usage(people, ignores, command):
    _, message = run(f"root# {command}", people)
    assert USAGE["ban"] in replied(message)[0]
    assert marks(message) == ["❌"] and ignores.active() == []


def test_owners_and_the_bot_itself_cannot_be_banned(people, ignores):
    bot_id = 999999999999999992
    people.client.user.id = bot_id
    known = {CO_OWNER: make_user(CO_OWNER, "owner"), bot_id: make_user(bot_id, "milky")}
    previous_lookup = people.client.get_user
    # Оба «известны» Discord и не являются ботами: остановить бан может только сама защита.
    people.client.get_user = lambda uid: known.get(uid) or previous_lookup(uid)

    for target in (CO_OWNER, bot_id):
        _, message = run(f"root# ban {target}", people)
        assert marks(message) == ["❌"] and replied(message) == []
    assert ignores.active() == []
    people.client.fetch_user.assert_not_awaited()      # отказ случился до запроса к Discord


def test_bots_cannot_be_banned(people, ignores):
    _, message = run(f"root# ban {U2}", people)
    assert marks(message) == ["❌"] and ignores.active() == []


def test_unknown_user_is_not_banned(people, ignores):
    _, message = run("root# ban 123123123123123123", people)   # такого пользователя нет в Discord
    assert marks(message) == ["❌"] and ignores.active() == []


def test_ban_without_client_fails_cleanly(people, ignores):
    _, message = run(f"root# ban {U1}", people, client=None)
    assert marks(message) == ["❌"] and ignores.active() == []


def test_ban_when_saving_fails_gives_cross_mark(people, ignores, monkeypatch):
    def broken_add(*args, **kwargs):
        raise OSError("диск переполнен")

    monkeypatch.setattr(ignores, "add", broken_add)
    _, message = run(f"root# ban {U1}", people)
    assert marks(message) == ["❌"] and replied(message) == []


def test_nickname_with_markdown_is_escaped_in_reply(people, ignores):
    people.client.get_user = lambda uid: make_user(U1, global_name="*жирный*")
    _, message = run(f"root# ban {U1}", people)
    assert "\\*жирный\\*" in replied(message)[0]


# ----------------------------------------------------------------------- unban

def test_unban_removes_and_confirms(people, ignores):
    run(f"root# ban {U1}", people)
    _, message = run(f"root# unban {U1}", people)
    assert not ignores.is_ignored(U1)
    assert "снова отвечает" in replied(message)[0] and "Алекс" in replied(message)[0]
    assert marks(message) == ["✅"]


def test_unban_by_mention(people, ignores):
    run(f"root# ban {U1}", people)
    run(f"root# unban <@{U1}>", people)
    assert not ignores.is_ignored(U1)


def test_unban_of_someone_not_in_list(people, ignores):
    _, message = run(f"root# unban {U1}", people)
    assert marks(message) == ["❌"] and replied(message) == []


@pytest.mark.parametrize("command", ["unban", "unban привет", f"unban {U1} лишнее"])
def test_unban_wrong_format(people, ignores, command):
    run(f"root# ban {U1}", people)
    _, message = run(f"root# {command}", people)
    assert USAGE["unban"] in replied(message)[0] and marks(message) == ["❌"]
    assert ignores.is_ignored(U1)                        # ничего не снято


# ------------------------------------------------------------------------ bans

def test_bans_when_empty(people, ignores):
    _, message = run("root# bans", people)
    assert replied(message) == ["Список игнорируемых пуст."]
    assert marks(message) == []


def test_bans_lists_everyone_with_terms_and_reasons(people, ignores):
    run(f"root# ban {U1} 7д спам в чате", people)
    run(f"root# ban {U3} оскорбления", people)
    _, message = run("root# bans", people)

    text = replied(message)[0]
    assert "Игнорируемые (2)" in text
    assert f"(`{U1}`)" in text and "Алекс" in text and "спам в чате" in text
    assert "до " in text and "ещё 6 д 23 ч" in text      # остаток срока показан (7 суток минус доли секунды)
    assert f"(`{U3}`)" in text and "dasha" in text and "бессрочно" in text
    assert message.reply.await_args.kwargs["delete_after"] == 60                  # список сам исчезает
    assert message.reply.await_args.kwargs["allowed_mentions"].users is False


def test_bans_does_not_ping_people_mentioned_in_reasons(people, ignores):
    ignores.add(U1, "Алекс", reason=f"спорил с <@{U3}> и @everyone")
    _, message = run("root# bans", people)
    kwargs = message.reply.await_args.kwargs
    assert kwargs["allowed_mentions"].users is False and kwargs["allowed_mentions"].everyone is False


def test_bans_with_a_lot_of_entries_fits_into_one_message(people, ignores):
    for i in range(80):
        ignores.add(700000000000000000 + i, f"Участник номер {i}", reason="причина " * 20)
    _, message = run("root# bans", people)
    text = replied(message)[0]
    assert len(text) <= 2000 and "…и ещё " in text


def test_expired_bans_are_not_shown(people, ignores):
    from datetime import datetime, timezone

    ignores.add(U1, "Истёк", hours=1, now=datetime.now(timezone.utc) - timedelta(hours=3))
    _, message = run("root# bans", people)
    assert replied(message) == ["Список игнорируемых пуст."]
