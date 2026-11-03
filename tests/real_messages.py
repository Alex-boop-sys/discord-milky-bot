"""Настоящие объекты discord.Message, собранные из «сырых» данных, как их присылает Discord.

Так тесты проверяют код на реальных классах discord.py (эмбеды, компоненты v2, флаги,
interaction_metadata), а не только на упрощённых подделках.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from discord.state import ConnectionState

CARL_BOT = 235148962103951360
DISCODUS = 1159147139960676422
DISBOARD = 302050872383242240
GUILD = 1000                      # сервер с экономикой (см. tests/conftest.py)
BUMPER = 777000000000000001       # тот, кто вызвал /bump
LOADING, COMPONENTS_V2 = 128, 32768   # флаги сообщения Discord: «бот думает» и компоненты v2

_state = ConnectionState(
    dispatch=lambda *a, **k: None, handlers={}, hooks={}, http=None, intents=discord.Intents.default()
)
_next_id = [900000000000000000]


def next_id():
    _next_id[0] += 1
    return _next_id[0]


def make_real_message(
    *,
    message_id=None,
    bot_id=CARL_BOT,
    bot_name="Carl-bot",
    content="",
    embeds=(),
    components=(),
    flags=0,
    initiator=BUMPER,
    guild_id=GUILD,
    channel_id=5,
    is_bot=True,
):
    """Реальное discord.Message (интерактивный ответ бота). initiator=None - без interaction_metadata."""
    raw = {
        "id": str(message_id or next_id()),
        "channel_id": str(channel_id),
        "type": 20 if initiator else 0,
        "flags": flags,
        "content": content,
        "tts": False,
        "pinned": False,
        "mention_everyone": False,
        "mentions": [],
        "mention_roles": [],
        "attachments": [],
        "embeds": list(embeds),
        "components": list(components),
        "timestamp": "2026-11-02T10:00:00.000000+00:00",
        "edited_timestamp": None,
        "author": {"id": str(bot_id), "username": bot_name, "discriminator": "0", "avatar": None, "bot": is_bot},
    }
    if initiator:
        raw["interaction_metadata"] = {
            "id": str(next_id()),
            "type": 2,
            "user": {"id": str(initiator), "username": "alex", "discriminator": "0", "avatar": None},
            "authorizing_integration_owners": {},
        }
    channel = discord.PartialMessageable(state=_state, id=channel_id, guild_id=guild_id)
    message = discord.Message(state=_state, channel=channel, data=raw)
    # Сервер и канал подменяем подделками: настоящих серверов без подключения к Discord нет.
    message.guild = SimpleNamespace(id=guild_id, name="Тестовый сервер", get_member=lambda uid: None) if guild_id else None
    message.channel = SimpleNamespace(id=channel_id, name="бампы", send=AsyncMock())
    return message


def container(*texts):
    """Компонент v2: контейнер с текстовыми блоками."""
    return {"type": 17, "components": [{"type": 10, "content": text} for text in texts]}


def embed(**fields):
    """Эмбед в «сыром» виде (title, description, fields=[(имя, значение)], footer, author)."""
    data = {key: value for key, value in fields.items() if key in ("title", "description")}
    if "fields" in fields:
        data["fields"] = [{"name": n, "value": v, "inline": False} for n, v in fields["fields"]]
    if "footer" in fields:
        data["footer"] = {"text": fields["footer"]}
    if "author" in fields:
        data["author"] = {"name": fields["author"]}
    return data
