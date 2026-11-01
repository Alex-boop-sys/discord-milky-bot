"""Общие подделки для тестов: сообщения и каналы Discord (без подключения к Discord)."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

BOT = SimpleNamespace(id=999)


class FakeChannel:
    def __init__(self, nsfw=False, reply_author_id=None, fetch_error=None):
        self._nsfw = nsfw
        if fetch_error is not None:
            self.fetch_message = AsyncMock(side_effect=fetch_error)
        else:
            author = SimpleNamespace(id=reply_author_id if reply_author_id is not None else BOT.id)
            self.fetch_message = AsyncMock(return_value=SimpleNamespace(author=author))

    def is_nsfw(self):
        return self._nsfw

    @asynccontextmanager
    async def typing(self):
        yield


def make_message(content, reply_to=None, attachments=(), nsfw=False, author_id=5):
    """Сообщение пользователя.

    reply_to: None - обычное сообщение; "bot" - ответ на сообщение Милки;
    "other" - ответ на сообщение другого человека; "missing" - исходное сообщение удалено.
    """
    channel_args = {"nsfw": nsfw}
    reference = None
    if reply_to is not None:
        reference = SimpleNamespace(message_id=7)
        if reply_to == "other":
            channel_args["reply_author_id"] = 123
        elif reply_to == "missing":
            channel_args["fetch_error"] = discord.NotFound(
                SimpleNamespace(status=404, reason="Not Found"), "нет"
            )
    return SimpleNamespace(
        id=1,
        content=content,
        reference=reference,
        attachments=list(attachments),
        channel=FakeChannel(**channel_args),
        guild=None,
        author=SimpleNamespace(id=author_id, display_name="Алекс", bot=False),
        reply=AsyncMock(),
        add_reaction=AsyncMock(),
    )
