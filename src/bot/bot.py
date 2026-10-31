# src/bot/bot.py
import discord
from discord import app_commands
from discord.ext import commands

from config import env, var
from src.bot.handlers.guilds import guild_handlers
from src.bot.handlers.messages import handlers
from src.utils.json_parser import dicts
from src.utils.logger import place_name, set_place
from src.utils.utils import utils


class MilkyTree(app_commands.CommandTree):
    """Дерево слэш-команд с общей проверкой перед КАЖДОЙ командой."""

    async def interaction_check(self, interaction):
        # Для логов: на каком сервере и в каком канале вызвана команда.
        set_place(place_name(interaction.guild, interaction.channel))
        # Страховка: бот ещё мог не успеть выйти с чужого сервера.
        if not utils.is_guild_allowed(interaction.guild_id):
            await interaction.response.send_message(
                dicts.get("not_available_here"), ephemeral=True
            )
            return False
        return True


bot = commands.Bot(
    command_prefix=commands.when_mentioned_or("/"),
    intents=var.intents,
    case_insensitive=True,
    tree_cls=MilkyTree,
    # Бот не может пинговать @everyone/@here и роли (в том числе по просьбе ИИ).
    # Упоминания людей и пинг автора при ответе остаются.
    allowed_mentions=discord.AllowedMentions(
        everyone=False, roles=False, users=True, replied_user=True
    ),
)


class BotHandlers:
    @staticmethod
    async def run_bot():
        await bot.start(env.discord_token)

    @staticmethod
    def register_handlers():
        @bot.event
        async def setup_hook():
            # Один раз при запуске, до on_ready (там же синхронизируются команды).
            await handlers.setup(bot)

        @bot.event
        async def on_message(message):
            await handlers.on_msg(message)

        @bot.event
        async def on_ready():
            await handlers.on_ready(bot)

        @bot.event
        async def on_guild_join(guild):
            await guild_handlers.on_join(guild)

        @bot.event
        async def on_raw_reaction_add(payload):
            await handlers.on_raw_reaction_add(payload)


bot_handlers = BotHandlers()
