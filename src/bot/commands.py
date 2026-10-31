# src/bot/func/commands.py
import discord
from discord import app_commands

from src.bot.bot import bot
from src.features.discord_actions import defs
from src.utils.json_parser import dicts
from src.utils.utils import utils


# КОМАНДЫ
def register_slash_commands():
    # @bot.tree.command(name="спросить", description="Отправить вопрос Экспи")
    # @app_commands.describe(request="Ваш вопрос для Экспи")
    # async def ask(interaction: discord.Interaction, request: str):
    #     await defs.ask(interaction, request)

    @bot.tree.command(name="забыть", description="Забыть контекст")
    async def forget(interaction: discord.Interaction):
        await defs.forget(interaction)

    @bot.tree.command(name="арт", description="Найти арт на e621")
    @app_commands.describe(query="Теги для поиска (опционально)")
    async def cmd_art(interaction: discord.Interaction, query: str = None):
        await defs.art(interaction, query)

    # @bot.tree.command(name="ген", description="Сгенерировать картинку с Экспи")
    # @app_commands.describe(prompt="Описание того, что нарисовать")
    # async def cmd_generate(interaction: discord.Interaction, prompt: str = None):
    #     await defs.generate(interaction, prompt)

    @bot.tree.command(name="фурь", description="Найти SFW арты на Furbooru")
    @app_commands.describe(query="Теги для поиска (на английском)")
    async def cmd_fur(interaction: discord.Interaction, query: str = None):
        await defs.fur(interaction, query)

    # @bot.tree.command(
    #     name="шип", description="Выбрать случайную пару из онлайн-пользователей"
    # )
    # async def cmd_pair(interaction: discord.Interaction):
    #     await defs.pair(interaction)

    # @bot.tree.command(name="обнять", description="Обнять кого-то")
    # @app_commands.describe(member="Кого обнять (опционально)")
    # async def cmd_hug(interaction: discord.Interaction, member: discord.User = None):
    #     await defs.hug(interaction, member)

    @bot.tree.command(name="стат", description="Статистика бесед")
    @app_commands.guild_only()
    async def cmd_stats(interaction: discord.Interaction):
        await defs.stats(interaction)

    @bot.tree.command(name="баланс", description="Показать количество Геофруктов 🫐")
    @app_commands.guild_only()
    async def cmd_balance(interaction: discord.Interaction):
        from src.features.economy import economy

        if not economy.enabled_in(interaction.guild_id):
            await interaction.response.send_message(
                dicts.get("not_available_here"), ephemeral=True
            )
            return

        balance = economy.get_balance(interaction.user.id)
        await interaction.response.send_message(
            f"🫐 У тебя **{balance}** {economy.currency_word(balance)}.",
            ephemeral=True,
        )

    @bot.tree.command(
        name="топ-бамперов", description="Топ-10 игроков по Геофруктам 🫐"
    )
    @app_commands.guild_only()
    async def cmd_bump_top(interaction: discord.Interaction):
        from src.features.economy import economy

        if not economy.enabled_in(interaction.guild_id):
            await interaction.response.send_message(
                dicts.get("not_available_here"), ephemeral=True
            )
            return

        top_users = economy.get_top(10)
        if not top_users:
            await interaction.response.send_message(
                "Пока никто не заработал Геофрукты за бампы 🫐"
            )
            return

        lines = ["**🫐 Топ-10 бамперов по Геофруктам**", ""]
        for place, row in enumerate(top_users, 1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, f"{place}.")
            count = row["balance"]
            word = economy.currency_word(count)
            # Баланс общий, а игрок может быть с другого сервера: тогда имя скрыто.
            name = utils.member_display_name(interaction.guild, row["user_id"])
            lines.append(f"{medal} **{name}** — **{count}** {word} 🫐")

        # Discord требует реальные символы перевода строки, а не буквальный \n.
        await interaction.response.send_message("\n".join(lines))
