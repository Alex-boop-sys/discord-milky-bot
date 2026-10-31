"""Проверки сборки бота: без подключения к Discord."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from config import env, var
from src.bot.bot import MilkyTree, bot, bot_handlers
from src.bot.handlers import messages as messages_module
from src.bot.handlers.messages import MessageHandlers
from src.features import discord_actions
from src.features.discord_actions import Defs
from src.utils.cooldown import Cooldown
from src.utils.utils import HIDDEN_USER_NAME, utils


def test_bot_never_pings_everyone_or_roles():
    mentions = bot.allowed_mentions
    assert mentions.everyone is False
    assert mentions.roles is False


def test_setup_hook_is_registered():
    original = bot.setup_hook
    bot_handlers.register_handlers()
    assert bot.setup_hook is not original                 # наш setup_hook заменил стандартный
    assert asyncio.iscoroutinefunction(bot.setup_hook)


def test_setup_hook_syncs_commands_once(monkeypatch):
    bot_handlers.register_handlers()
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(bot.tree, "sync", sync)
    asyncio.run(bot.setup_hook())
    sync.assert_awaited_once()


class FakeChannel:
    def __init__(self, nsfw=False):
        self._nsfw = nsfw

    def is_nsfw(self):
        return self._nsfw

    @asynccontextmanager
    async def typing(self):
        yield


@pytest.fixture
def fake_features(monkeypatch):
    art, gen = AsyncMock(), AsyncMock()
    monkeypatch.setattr(messages_module.art_search, "art", art)
    monkeypatch.setattr(messages_module.image_generation, "generate", gen)
    return art, gen


def run_trigger(text, nsfw_channel=False):
    message = SimpleNamespace(channel=FakeChannel(nsfw_channel), reply=AsyncMock())
    handled = asyncio.run(MessageHandlers.handle_triggers(message, text, None))
    return handled, message


def test_ordinary_text_goes_to_ai(fake_features):
    art, gen = fake_features
    handled, _ = run_trigger("артём, привет")
    assert handled is False
    art.assert_not_called()
    gen.assert_not_called()


def test_art_trigger_calls_art_search(fake_features):
    art, _ = fake_features
    handled, message = run_trigger("арт лиса")
    assert handled is True
    art.assert_awaited_once_with(message, "лиса")


def test_generation_trigger_calls_image_generation(fake_features):
    _, gen = fake_features
    handled, message = run_trigger("создай картинку про осень")
    assert handled is True
    gen.assert_awaited_once_with(message, "про осень")


def test_nsfw_request_is_refused_in_ordinary_channel(fake_features):
    art, _ = fake_features
    handled, message = run_trigger("арт nsfw лиса", nsfw_channel=False)
    assert handled is True
    art.assert_not_called()
    message.reply.assert_awaited_once()


def test_nsfw_request_is_allowed_in_nsfw_channel(fake_features):
    art, _ = fake_features
    handled, _ = run_trigger("арт nsfw лиса", nsfw_channel=True)
    assert handled is True
    art.assert_awaited_once()


# ---------- несколько серверов -------------------------------------------------

def test_guild_join_handler_is_registered():
    bot_handlers.register_handlers()
    assert asyncio.iscoroutinefunction(bot.on_guild_join)


def test_slash_commands_are_checked_by_milky_tree(monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [1])
    assert isinstance(bot.tree, MilkyTree)

    def interaction(guild_id):
        return SimpleNamespace(
            guild=None, channel=None, guild_id=guild_id,
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    allowed, stranger = interaction(1), interaction(99)
    assert asyncio.run(bot.tree.interaction_check(allowed)) is True
    allowed.response.send_message.assert_not_called()
    assert asyncio.run(bot.tree.interaction_check(stranger)) is False
    stranger.response.send_message.assert_awaited_once()           # пишем «Это пока не работает тут»


def test_member_display_name_hides_foreign_users():
    guild = SimpleNamespace(get_member=lambda uid: SimpleNamespace(display_name="Алекс") if uid == 1 else None)
    assert utils.member_display_name(guild, 1) == "Алекс"
    assert utils.member_display_name(guild, 2) == HIDDEN_USER_NAME == "Пользователь скрыт"
    assert utils.member_display_name(None, 1) == HIDDEN_USER_NAME


# ---------- таймер ----------------------------------------------------------------

def test_message_cooldown_warns_once_and_blocks(monkeypatch):
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(messages_module, "cooldown", Cooldown())
    message = SimpleNamespace(author=SimpleNamespace(id=5), reply=AsyncMock())

    assert asyncio.run(MessageHandlers._cooldown_ok(message)) is True
    message.reply.assert_not_called()
    assert asyncio.run(MessageHandlers._cooldown_ok(message)) is False
    assert message.reply.await_count == 1                          # предупреждение отправлено
    assert message.reply.await_args.kwargs["delete_after"] == 5    # и стирается само
    assert asyncio.run(MessageHandlers._cooldown_ok(message)) is False
    assert message.reply.await_count == 1                          # повторно не спамим


def test_slash_cooldown_answers_privately(monkeypatch):
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(discord_actions, "cooldown", Cooldown())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=5), response=SimpleNamespace(send_message=AsyncMock())
    )

    assert asyncio.run(Defs.cooldown_ok(interaction)) is True
    assert asyncio.run(Defs.cooldown_ok(interaction)) is False
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


# ---------- обращения к боту: сервер и таймер -----------------------------------------

BOT = SimpleNamespace(id=999)


def make_chat_message(content, reference=None):
    channel = FakeChannel()
    channel.fetch_message = AsyncMock(return_value=SimpleNamespace(author=SimpleNamespace(id=BOT.id)))
    return SimpleNamespace(
        id=1, content=content, reference=reference, attachments=[], channel=channel,
        author=SimpleNamespace(id=5, display_name="Алекс"), reply=AsyncMock(),
    )


def test_messages_from_unknown_servers_are_ignored(monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [1])
    check = AsyncMock()
    monkeypatch.setattr(MessageHandlers, "check_ping", check)
    message = SimpleNamespace(guild=SimpleNamespace(id=99, name="Чужой"), channel=SimpleNamespace(name="общий"))
    asyncio.run(MessageHandlers().on_msg(message))
    check.assert_not_called()


@pytest.mark.parametrize(
    "content, reference",
    [
        ("Милки, привет", None),                           # обращение по имени
        (f"<@{BOT.id}> привет", None),                     # через @
        ("привет", SimpleNamespace(message_id=7)),         # ответом на сообщение бота
    ],
)
def test_every_way_to_chat_is_limited_by_cooldown(monkeypatch, content, reference):
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(env, "owner_ids", [])
    monkeypatch.setattr(messages_module, "cooldown", Cooldown())
    ask = AsyncMock(return_value="ответ")
    monkeypatch.setattr(messages_module.api, "ask_ai", ask)

    message = make_chat_message(content, reference)
    handlers = MessageHandlers()
    for _ in range(3):                                     # три обращения подряд
        asyncio.run(handlers.check_ping(message, BOT))

    assert ask.await_count == 1                            # ИИ позвали только раз
    notices = [c for c in message.reply.await_args_list if "delete_after" in c.kwargs]
    assert len(notices) == 1                               # и предупредили тоже один раз


@pytest.mark.parametrize("command", ["art", "generate", "fur"])
def test_heavy_slash_commands_respect_cooldown(monkeypatch, command):
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(env, "owner_ids", [])
    used_cooldown = Cooldown()
    monkeypatch.setattr(discord_actions, "cooldown", used_cooldown)
    monkeypatch.setattr(discord_actions.art_search, "art_interaction", AsyncMock())
    monkeypatch.setattr(discord_actions.image_generation, "generate_interaction", AsyncMock())
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=5),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
    )

    used_cooldown.check(5)                                 # человек только что уже обращался к боту
    asyncio.run(getattr(Defs(), command)(interaction, "wolf"))

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.defer.assert_not_called()         # тяжёлая работа не начиналась
    discord_actions.art_search.art_interaction.assert_not_called()
    discord_actions.image_generation.generate_interaction.assert_not_called()


def test_startup_leaves_unknown_servers(monkeypatch):
    monkeypatch.setattr(var, "allowed_guild_ids", [1])
    monkeypatch.setattr(MessageHandlers, "resume_reaction_events", AsyncMock())
    monkeypatch.setattr(messages_module.morning_greetings, "start", AsyncMock())

    def guild(guild_id):
        return SimpleNamespace(id=guild_id, name=f"С{guild_id}", leave=AsyncMock())

    ours, stranger = guild(1), guild(99)
    fake_bot = SimpleNamespace(user="Милки", guilds=[ours, stranger], change_presence=AsyncMock())
    asyncio.run(MessageHandlers().on_ready(fake_bot))

    ours.leave.assert_not_called()
    stranger.leave.assert_awaited_once()
