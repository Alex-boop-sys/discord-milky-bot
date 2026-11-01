"""Путь сообщения: как бот понимает обращение, что игнорирует и что отвечает."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from config import env, var
from src.bot.handlers import messages as messages_module
from src.bot.handlers.messages import MessageHandlers
from src.services.ignore_list import IgnoreList
from src.utils.cooldown import Cooldown
from src.utils.json_parser import dicts
from tests.helpers import BOT, make_message


@pytest.fixture
def ask(monkeypatch):
    """Подставной ИИ и чистый таймер. Возвращает мок ask_ai."""
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(env, "owner_ids", [])
    monkeypatch.setattr(messages_module, "cooldown", Cooldown())
    mock = AsyncMock(return_value="ответ")
    monkeypatch.setattr(messages_module.api, "ask_ai", mock)
    return mock


def say(message):
    asyncio.run(MessageHandlers().check_ping(message, BOT))


def find(message):
    return asyncio.run(MessageHandlers.find_request(message, BOT))


# ------------------------------------------------------------ find_request

@pytest.mark.parametrize(
    "content, expected",
    [
        ("Милки, привет", ("name", "привет")),          # запятая после имени отбрасывается
        ("милки привет", ("name", "привет")),
        ("Milky hi", ("name", "hi")),
        ("Милки: привет", ("name", ": привет")),
        (f"<@{BOT.id}> привет", ("mention", "привет")),
        (f"<@!{BOT.id}>, привет", ("mention", "привет")),
        ("просто текст", None),
        ("привет, Милки", None),                         # имя не в начале
    ],
)
def test_find_request_by_text(content, expected):
    assert find(make_message(content)) == expected


def test_find_request_for_reply_to_bot():
    assert find(make_message("привет", reply_to="bot")) == ("reply", "привет")


def test_reply_to_someone_else_is_not_an_address():
    assert find(make_message("привет", reply_to="other")) is None


def test_reply_to_someone_else_can_still_mention_the_bot_by_name():
    assert find(make_message("Милки, привет", reply_to="other")) == ("name", "привет")


def test_deleted_original_message_does_not_break_search():
    assert find(make_message("Милки, привет", reply_to="missing")) == ("name", "привет")


# ------------------------------------------- чужие команды и «\» игнорируются

PATHS = [
    ("name", lambda text: make_message(f"Милки {text}")),
    ("mention", lambda text: make_message(f"<@{BOT.id}> {text}")),
    ("reply", lambda text: make_message(text, reply_to="bot")),
]


@pytest.mark.parametrize("path, build", PATHS, ids=[p for p, _ in PATHS])
@pytest.mark.parametrize("text", ["!play song", ".r d20", "/help", "-skip", ";p", "\\не отвечай", "\\"])
def test_foreign_commands_are_ignored(ask, path, build, text):
    message = build(text)
    say(message)
    ask.assert_not_called()
    message.reply.assert_not_called()                   # молчим совсем, даже без предупреждений


@pytest.mark.parametrize("path, build", PATHS, ids=[p for p, _ in PATHS])
@pytest.mark.parametrize("text", ["привет", ": привет", "- привет", "! привет", ". Привет", "...ну ладно", ":)", ";)", "!!!"])
def test_ordinary_text_with_punctuation_is_answered(ask, path, build, text):
    say(build(text))
    ask.assert_awaited_once()


def test_comma_and_colon_after_name_are_answered(ask):
    for text in ("Милки, привет", "Милки: привет", "Милки - привет", "Милки! привет"):
        ask.reset_mock()
        ask.return_value = "ответ"
        messages_module.cooldown = Cooldown()              # свежий таймер на каждую фразу
        say(make_message(text))
        assert ask.await_count == 1, text


def test_ignored_message_does_not_use_up_the_cooldown(ask):
    say(make_message("Милки !play song"))
    message = make_message("Милки, привет")
    say(message)
    ask.assert_awaited_once()                           # сразу отвечено, таймер не тронут
    assert not [c for c in message.reply.await_args_list if "delete_after" in c.kwargs]


# ----------------------------------------------------------- остальной путь

@pytest.mark.parametrize("content, reply_to", [("Милки", None), (f"<@{BOT.id}>", None), ("", "bot")])
def test_empty_address_gets_a_short_reply_without_ai(ask, content, reply_to):
    message = make_message(content, reply_to=reply_to)
    say(message)
    ask.assert_not_called()
    assert message.reply.await_args.args[0] in dicts.get("ping_an")


def test_attachment_without_text_gets_image_reply(ask):
    message = make_message("Милки", attachments=["картинка.png"])
    say(message)
    ask.assert_not_called()
    assert message.reply.await_args.args[0] in dicts.get("image_responses")


def test_word_commands_work_in_direct_address_but_not_in_replies(ask, monkeypatch):
    art = AsyncMock()
    monkeypatch.setattr(messages_module.art_search, "art", art)

    direct = make_message("Милки, арт лиса")
    say(direct)
    art.assert_awaited_once_with(direct, "лиса")
    ask.assert_not_called()

    art.reset_mock()
    messages_module.cooldown = Cooldown()
    say(make_message("арт лиса", reply_to="bot"))        # ответом - как и раньше, всё идёт в ИИ
    art.assert_not_called()
    ask.assert_awaited_once()


def test_ai_answer_is_sent_as_reply(ask):
    message = make_message("Милки, привет")
    say(message)
    ask.assert_awaited_once_with(5, "Алекс", "привет")
    message.reply.assert_awaited_once_with("ответ")


def test_missing_permissions_do_not_crash_the_bot(ask):
    message = make_message("Милки, привет")
    message.reply = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "нет прав"))
    say(message)                                          # исключение не вылетает наружу


# --------------------------------------------------------------- игнор-лист

@pytest.fixture
def ignores(monkeypatch, tmp_path):
    """Игнор-лист во временной папке и «записыватель» в лог, чтобы проверять сообщения."""
    ignore_list = IgnoreList(str(tmp_path / "ignored.json"))
    monkeypatch.setattr(messages_module, "ignore_list", ignore_list)
    monkeypatch.setattr(messages_module, "log", SimpleNamespace(info=Mock(), warning=Mock(), error=Mock()))
    return ignore_list


@pytest.mark.parametrize("path, build", PATHS, ids=[p for p, _ in PATHS])
def test_ignored_user_gets_no_answer_by_any_path(ask, ignores, path, build):
    ignores.add(5, "Алекс")
    message = build("привет")
    say(message)
    ask.assert_not_called()
    message.reply.assert_not_called()                   # молчим совсем


def test_other_people_are_not_affected_by_ignore(ask, ignores):
    ignores.add(123, "Кто-то другой")
    say(make_message("Милки, привет"))
    ask.assert_awaited_once()


def test_ban_and_unban_work_immediately_without_restart(ask, ignores):
    ignores.add(5, "Алекс")
    say(make_message("Милки, привет"))
    ask.assert_not_called()

    ignores.remove(5)
    say(make_message("Милки, привет"))
    ask.assert_awaited_once()


def test_expired_ban_no_longer_applies(ask, ignores):
    ignores.add(5, "Алекс", hours=1, now=datetime.now(timezone.utc) - timedelta(hours=2))
    say(make_message("Милки, привет"))
    ask.assert_awaited_once()


def test_ignored_attempt_is_logged_only_when_user_really_addresses_the_bot(ask, ignores):
    ignores.add(5, "Алекс")
    say(make_message("просто болтаю с друзьями"))       # к Милки не обращались
    messages_module.log.info.assert_not_called()

    say(make_message("Милки, привет"))
    say(make_message("<@999> привет"))
    assert messages_module.log.info.call_count == 2     # по записи на каждое настоящее обращение
    assert "5" in messages_module.log.info.call_args.args[0]


# ------------------------------------------------------------------ on_msg

def test_on_msg_uses_given_bot_user_and_skips_bots(monkeypatch):
    check = AsyncMock()
    monkeypatch.setattr(MessageHandlers, "check_ping", check)
    monkeypatch.setattr(MessageHandlers, "handle_economy", AsyncMock())

    human = make_message("привет")
    asyncio.run(MessageHandlers().on_msg(human, BOT))
    check.assert_awaited_once_with(human, BOT)

    check.reset_mock()
    asyncio.run(MessageHandlers().on_msg(human, None))    # бот не известен и это не сервер
    check.assert_not_called()

    robot = make_message("привет")
    robot.author.bot = True
    asyncio.run(MessageHandlers().on_msg(robot, BOT))
    check.assert_not_called()


def test_economy_reward_is_announced_with_mention(monkeypatch):
    fake_economy = SimpleNamespace(is_reaction_bump_success=lambda m: False, process_bump_message=lambda m: 42)
    monkeypatch.setattr(messages_module, "economy", fake_economy)
    message = SimpleNamespace(guild=None, channel=SimpleNamespace(send=AsyncMock()))
    asyncio.run(MessageHandlers().handle_economy(message))
    text = message.channel.send.await_args.args[0]
    assert "<@42>" in text and "Геофрукт" in text


def test_reaction_bump_starts_event_instead_of_reward(monkeypatch):
    fake_economy = SimpleNamespace(is_reaction_bump_success=lambda m: True, process_bump_message=lambda m: 1 / 0)
    monkeypatch.setattr(messages_module, "economy", fake_economy)
    start = AsyncMock()
    monkeypatch.setattr(MessageHandlers, "start_reaction_bump_event", start)
    message = SimpleNamespace(guild=None, channel=SimpleNamespace(send=AsyncMock()))
    asyncio.run(MessageHandlers().handle_economy(message))
    start.assert_awaited_once_with(message)
