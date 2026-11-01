"""Административные команды root#: разбор ID, формат, права и результат (✅/❌)."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from config import env, paths
from src.bot import admin_commands as admin_module
from src.bot.admin_commands import AdminError, HELP_TEXT, USAGE, admin_commands, split_ids
from src.bot.handlers import messages as messages_module
from src.bot.handlers.messages import MessageHandlers
from tests.helpers import BOT, make_message

OWNER = 5
G1, C1, C2 = 111111111111111111, 222222222222222222, 333333333333333333   # сервер 1 и его каналы
G2, C3 = 444444444444444444, 555555555555555555                           # сервер 2 и его канал
M1, M2 = 666666666666666666, 777777777777777777                           # сообщение Милки и чужое


def http_error(error_class, status):
    return error_class(SimpleNamespace(status=status, reason="x"), "ошибка")


def fake_message(message_id, author_id):
    return SimpleNamespace(
        id=message_id,
        author=SimpleNamespace(id=author_id),
        reply=AsyncMock(),
        add_reaction=AsyncMock(),
        delete=AsyncMock(),
    )


class World:
    """Два сервера, три канала и два сообщения - всё как подделки, без Discord."""

    def __init__(self):
        self.milky_message = fake_message(M1, BOT.id)
        self.other_message = fake_message(M2, 123)

        def fetch_message(message_id):
            if message_id == M1:
                return self.milky_message
            if message_id == M2:
                return self.other_message
            raise http_error(discord.NotFound, 404)

        def channel(channel_id, guild):
            ch = SimpleNamespace(id=channel_id, name=f"канал-{channel_id % 1000}", guild=guild, send=AsyncMock())
            ch.fetch_message = AsyncMock(side_effect=fetch_message)
            return ch

        not_found = AsyncMock(side_effect=http_error(discord.NotFound, 404))
        self.guild1 = SimpleNamespace(id=G1, name="Первый")
        self.guild2 = SimpleNamespace(id=G2, name="Второй")
        self.c1, self.c2 = channel(C1, self.guild1), channel(C2, self.guild1)
        self.c3 = channel(C3, self.guild2)
        channels = {C1: self.c1, C2: self.c2, C3: self.c3}
        for guild, ids in ((self.guild1, (C1, C2)), (self.guild2, (C3,))):
            guild.get_channel_or_thread = lambda cid, ids=ids: channels[cid] if cid in ids else None
            guild.fetch_channel = not_found
        self.client = SimpleNamespace(
            user=SimpleNamespace(id=BOT.id),
            get_channel=lambda cid: channels.get(cid),
            get_guild=lambda gid: {G1: self.guild1, G2: self.guild2}.get(gid),
            fetch_channel=not_found,
        )


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(env, "owner_ids", [OWNER])
    return World()


def run(text, world, author_id=OWNER, client="world"):
    """Запускает команду; text - то, что идёт после обращения к Милки."""
    message = make_message(text, author_id=author_id)
    client = world.client if client == "world" else client
    handled = asyncio.run(admin_commands.handle(message, text, client))
    return handled, message


def marks(message):
    return [c.args[0] for c in message.add_reaction.await_args_list]


def replied(message):
    return [c.args[0] for c in message.reply.await_args_list]


# --------------------------------------------------------------- разбор ID

@pytest.mark.parametrize(
    "args, limit, ids, rest",
    [
        (f"{C1} текст", 2, [C1], "текст"),
        (f"{G1} {C1} текст", 2, [G1, C1], "текст"),
        (f"{G1} {C1} {M1} 👍", 3, [G1, C1, M1], "👍"),
        (f"<#{C1}> привет", 2, [C1], "привет"),
        (f"https://discord.com/channels/{G1}/{C1}/{M1} текст", 3, [G1, C1, M1], "текст"),
        (f"https://ptb.discord.com/channels/{G1}/{C1}/{M1}", 3, [G1, C1, M1], ""),
        (f"https://discordapp.com/channels/{G1}/{C1} текст", 2, [G1, C1], "текст"),
        (f"https://discord.com/channels/{G1}/{C1}/{M1} текст", 2, [], f"https://discord.com/channels/{G1}/{C1}/{M1} текст"),
        ("2026 год", 2, [], "2026 год"),                       # короткое число - это текст
        (f"{'1' * 21} текст", 2, [], f"{'1' * 21} текст"),     # слишком длинное - не ID
        (f"{C1}текст", 2, [], f"{C1}текст"),                   # ID вплотную к тексту - не ID
        (f"{G1} {C1} {M1}", 2, [G1, C1], str(M1)),             # больше лимита не читаем
        ("  ", 2, [], ""),
    ],
)
def test_split_ids(args, limit, ids, rest):
    assert split_ids(args, limit) == (ids, rest)


def test_links_to_direct_messages_are_rejected():
    with pytest.raises(AdminError):
        split_ids(f"https://discord.com/channels/@me/{C1}/{M1} текст", 3)


# ------------------------------------------------------------------ права

@pytest.mark.parametrize("command", [f"say {C1} привет", f"reply {M1} привет", f"reaction {M1} 👍", f"del {M1}", "log", "ver"])
def test_non_owner_cannot_use_any_command(world, command):
    handled, message = run(f"root# {command}", world, author_id=123)
    assert handled is True
    assert replied(message) == ["⛔ Эта административная команда тебе недоступна."]
    world.c1.send.assert_not_called()
    world.milky_message.delete.assert_not_called()
    world.milky_message.reply.assert_not_called()


def test_ordinary_text_is_not_a_command(world):
    handled, message = run("просто текст", world)
    assert handled is False
    message.reply.assert_not_called()


def test_ver_help_and_unknown_command(world):
    _, message = run("root# ver", world)
    assert "версии" in replied(message)[0]
    for text in ("root# help", "root#", "root# что-то-непонятное"):
        _, message = run(text, world)
        assert replied(message) == [HELP_TEXT]
        assert marks(message) == []


def test_help_describes_every_command():
    for command in ("ver", "log", "say", "reply", "reaction", "del", "help"):
        assert f"root# {command}" in HELP_TEXT
    assert set(USAGE) == {"say", "reply", "reaction", "del"}


# --------------------------------------------------------------------- say

def test_say_sends_text_to_channel_and_marks_success(world):
    _, message = run(f"root# say {C1} Привет, МИР!", world)
    world.c1.send.assert_awaited_once_with("Привет, МИР!")
    assert marks(message) == ["✅"]
    message.reply.assert_not_called()                      # итог - только реакция


def test_say_keeps_text_exactly_and_command_name_is_case_insensitive(world):
    run(f"ROOT# SAY {C1} строка 1\nстрока  2   с пробелами", world)
    world.c1.send.assert_awaited_once_with("строка 1\nстрока  2   с пробелами")


def test_say_with_server_and_channel_mention_and_short_number_text(world):
    run(f"root# say {G2} {C3} привет", world)
    world.c3.send.assert_awaited_once_with("привет")
    run(f"root# say <#{C2}> 2026 год", world)
    world.c2.send.assert_awaited_once_with("2026 год")


@pytest.mark.parametrize(
    "command",
    [
        "say",                       # ничего
        f"say {C1}",                 # нет текста
        "say привет всем",           # нет ID
        f"say {G1} {C1}",            # нет текста
    ],
)
def test_say_wrong_format_shows_correct_format(world, command):
    _, message = run(f"root# {command}", world)
    text = replied(message)[0]
    assert text.startswith("Неверный формат") and USAGE["say"] in text
    assert marks(message) == ["❌"]
    for channel in (world.c1, world.c2, world.c3):
        channel.send.assert_not_called()


@pytest.mark.parametrize(
    "command, why",
    [
        ("say 999999999999999999 привет", "канал не найден"),
        (f"say 999999999999999999 {C1} привет", "нет такого сервера"),
        (f"say {G2} {C1} привет", "канал не на этом сервере"),
        (f"say {C1} {'я' * 2001}", "слишком длинный текст"),
    ],
)
def test_say_failures_give_cross_mark_and_do_not_send(world, command, why):
    _, message = run(f"root# {command}", world)
    assert marks(message) == ["❌"], why
    assert replied(message) == []                          # одна реакция, без лишних сообщений
    for channel in (world.c1, world.c2, world.c3):
        channel.send.assert_not_called()


def test_say_without_permission_gives_cross_mark(world):
    world.c1.send = AsyncMock(side_effect=http_error(discord.Forbidden, 403))
    _, message = run(f"root# say {C1} привет", world)
    assert marks(message) == ["❌"]


def test_say_without_client_gives_cross_mark(world):
    _, message = run(f"root# say {C1} привет", world, client=None)
    assert marks(message) == ["❌"]


def test_say_to_channel_without_text_support(world):
    category = SimpleNamespace(id=C1)                      # у категории нет send()
    world.client.get_channel = lambda cid: category
    _, message = run(f"root# say {C1} привет", world)
    assert marks(message) == ["❌"]


# ------------------------------------------------------------------- reply

def test_reply_in_current_channel_by_default(world):
    message = make_message(f"root# reply {M2} Согласна!", author_id=OWNER)
    message.channel.fetch_message = AsyncMock(return_value=world.other_message)
    asyncio.run(admin_commands.handle(message, f"root# reply {M2} Согласна!", world.client))
    message.channel.fetch_message.assert_awaited_once_with(M2)
    world.other_message.reply.assert_awaited_once_with("Согласна!")
    assert marks(message) == ["✅"]


@pytest.mark.parametrize(
    "target",
    [f"{C2} {M2}", f"{G1} {C2} {M2}", f"<#{C2}> {M2}", f"https://discord.com/channels/{G1}/{C2}/{M2}"],
)
def test_reply_with_channel_server_mention_or_link(world, target):
    _, message = run(f"root# reply {target} Привет, как дела", world)
    world.c2.fetch_message.assert_awaited_once_with(M2)
    world.other_message.reply.assert_awaited_once_with("Привет, как дела")
    assert marks(message) == ["✅"]


@pytest.mark.parametrize("command", ["reply", f"reply {M1}", f"reply {C1} {M1}", "reply привет"])
def test_reply_wrong_format(world, command):
    _, message = run(f"root# {command}", world)
    assert USAGE["reply"] in replied(message)[0]
    assert marks(message) == ["❌"]


def test_reply_to_missing_message_gives_cross_mark(world):
    _, message = run(f"root# reply {C1} 888888888888888888 привет", world)
    assert marks(message) == ["❌"]
    world.milky_message.reply.assert_not_called()


# ---------------------------------------------------------------- reaction

@pytest.mark.parametrize(
    "emoji, name, emoji_id, animated",
    [
        ("👍", "👍", None, False),
        ("<:milky:123456789012345678>", "milky", 123456789012345678, False),
        ("<a:dance:123456789012345678>", "dance", 123456789012345678, True),
    ],
)
def test_reaction_unicode_custom_and_animated_emoji(world, emoji, name, emoji_id, animated):
    _, message = run(f"root# reaction {C2} {M2} {emoji}", world)
    world.other_message.add_reaction.assert_awaited_once()
    sent = world.other_message.add_reaction.await_args.args[0]
    assert (sent.name, sent.id, sent.animated) == (name, emoji_id, animated)
    assert marks(message) == ["✅"]


def test_reaction_in_current_channel(world):
    message = make_message(f"root# reaction {M2} 👍", author_id=OWNER)
    message.channel.fetch_message = AsyncMock(return_value=world.other_message)
    asyncio.run(admin_commands.handle(message, f"root# reaction {M2} 👍", world.client))
    world.other_message.add_reaction.assert_awaited_once()
    assert world.other_message.add_reaction.await_args.args[0].name == "👍"


@pytest.mark.parametrize("command", ["reaction", f"reaction {M1}", f"reaction {M1} 👍 👎", "reaction 👍"])
def test_reaction_wrong_format(world, command):
    _, message = run(f"root# {command}", world)
    assert USAGE["reaction"] in replied(message)[0]
    assert marks(message) == ["❌"]


def test_reaction_unknown_emoji_gives_cross_mark(world):
    world.other_message.add_reaction = AsyncMock(side_effect=http_error(discord.HTTPException, 400))
    _, message = run(f"root# reaction {C2} {M2} абракадабра", world)
    assert marks(message) == ["❌"]


# --------------------------------------------------------------------- del

def test_del_removes_only_milkys_own_message(world):
    _, message = run(f"root# del {C1} {M1}", world)
    world.milky_message.delete.assert_awaited_once()
    assert marks(message) == ["✅"]


def test_del_refuses_other_peoples_messages(world):
    _, message = run(f"root# del {C1} {M2}", world)
    world.other_message.delete.assert_not_called()
    assert marks(message) == ["❌"]


def test_del_with_link_and_without_client(world):
    _, message = run(f"root# del https://discord.com/channels/{G1}/{C1}/{M1}", world)
    world.milky_message.delete.assert_awaited_once()
    world.milky_message.delete.reset_mock()
    _, message = run(f"root# del {C1} {M1}", world, client=None)   # клиента нет - узнать, чьё сообщение, нельзя
    assert marks(message) == ["❌"]
    world.milky_message.delete.assert_not_called()


@pytest.mark.parametrize("command", ["del", f"del {M1} лишнее", "del привет"])
def test_del_wrong_format(world, command):
    _, message = run(f"root# {command}", world)
    assert USAGE["del"] in replied(message)[0]
    assert marks(message) == ["❌"]
    world.milky_message.delete.assert_not_called()


def test_failed_cross_mark_itself_cannot_crash(world):
    message = make_message("root# say 999999999999999999 привет", author_id=OWNER)
    message.add_reaction = AsyncMock(side_effect=http_error(discord.Forbidden, 403))
    assert asyncio.run(admin_commands.handle(message, "root# say 999999999999999999 привет", world.client)) is True


# --------------------------------------------------------------------- log

@pytest.fixture
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "logs", str(tmp_path))
    return tmp_path / "milky.log"


def run_log(world):
    message = make_message("root# log", author_id=OWNER)
    message.author.send = AsyncMock()
    asyncio.run(admin_commands.handle(message, "root# log", world.client))
    return message


def test_log_goes_to_chat_as_text_and_disappears_after_a_minute(world, log_file):
    log_file.write_text("\n".join(f"строка {i}" for i in range(1, 31)) + "\n", encoding="utf-8")
    message = run_log(world)

    text = message.reply.await_args.args[0]
    assert text == "```\n" + "\n".join(f"строка {i}" for i in range(21, 31)) + "\n```"   # ровно 10 последних
    assert message.reply.await_args.kwargs["delete_after"] == 60
    assert message.reply.await_args.kwargs.get("file") is None
    assert marks(message) == ["📩"]                         # вместо сообщения про «личные сообщения»
    message.author.send.assert_not_called()                 # в личку больше не пишем


def test_log_strips_colors_and_cannot_break_the_code_block(world, log_file):
    log_file.write_text("\x1b[92m[INFO] привет\x1b[0m\nстрока с ``` внутри\n", encoding="utf-8")
    text = run_log(world).reply.await_args.args[0]
    assert "\x1b" not in text and "[INFO] привет" in text
    assert text.count("```") == 2                           # только открывающий и закрывающий


def test_long_log_is_cut_to_fit_and_keeps_newest_lines(world, log_file):
    log_file.write_text("".join(f"{i:02d} " + "x" * 400 + "\n" for i in range(30)), encoding="utf-8")
    text = run_log(world).reply.await_args.args[0]
    assert len(text) <= 2000
    assert text.startswith("```\n…") and text.endswith("x\n```")
    assert "29 " in text                                    # самая свежая строка на месте


def test_empty_or_missing_log(world, log_file):
    assert "Лог пока пуст." in run_log(world).reply.await_args.args[0]    # файла нет
    log_file.write_text("  \n", encoding="utf-8")
    assert "Лог пока пуст." in run_log(world).reply.await_args.args[0]    # файл пустой


def test_unreadable_log_gives_cross_mark(world, log_file, monkeypatch):
    log_file.write_text("строка\n", encoding="utf-8")

    def broken_open(self, *args, **kwargs):
        raise PermissionError("нет доступа")

    monkeypatch.setattr(Path, "open", broken_open)
    message = run_log(world)
    assert marks(message) == ["❌"]
    message.reply.assert_not_called()


# ------------------------------------------- через обычное обращение к Милки

def test_admin_command_works_through_normal_address_and_skips_ai(world, monkeypatch):
    ask = AsyncMock(return_value="ответ")
    monkeypatch.setattr(messages_module.api, "ask_ai", ask)
    handlers = MessageHandlers()
    handlers.client = world.client                          # так клиент попадает в setup()

    message = make_message(f"Милки root# say {C1} Привет из админки", author_id=OWNER)
    asyncio.run(handlers.check_ping(message, BOT))

    world.c1.send.assert_awaited_once_with("Привет из админки")
    assert marks(message) == ["✅"]
    ask.assert_not_called()


def test_setup_gives_the_client_to_handlers(monkeypatch):
    handlers = MessageHandlers()
    bot = SimpleNamespace(tree=SimpleNamespace(sync=AsyncMock(return_value=[])))
    asyncio.run(handlers.setup(bot))
    assert handlers.client is bot


def test_module_constants_match_requirements():
    assert admin_module.LOG_LIFETIME == 60 and admin_module.LOG_LINES == 10
