"""Общие подделки для тестов админ-команд: два сервера, каналы, сообщения и запуск команды."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from src.bot.admin_commands import admin_commands
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
