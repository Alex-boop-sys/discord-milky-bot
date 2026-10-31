"""Выход с серверов, которых нет в списке ALLOWED_GUILD_IDS."""
import discord

from config import var
from src.utils.logger import log
from src.utils.utils import utils


class GuildHandlers:
    @staticmethod
    async def leave_if_not_allowed(guild):
        """Выходит с сервера, если его нет в списке. Возвращает True, если сервер чужой."""
        if utils.is_guild_allowed(guild.id):
            return False
        log.warning(
            f"Сервер «{guild.name}» ({guild.id}) не входит в ALLOWED_GUILD_IDS, выхожу"
        )
        try:
            await guild.leave()
        except discord.HTTPException as exc:
            log.error(f"Не удалось выйти с сервера «{guild.name}» ({guild.id}): {exc}")
        return True

    async def on_join(self, guild):
        """Бота добавили на сервер."""
        if not await self.leave_if_not_allowed(guild):
            log.info(f"Бот добавлен на сервер «{guild.name}» ({guild.id})")

    async def sweep(self, bot):
        """Проверка при запуске: вдруг бота добавили, пока он был выключен."""
        if not var.allowed_guild_ids:
            log.warning("ALLOWED_GUILD_IDS пуст: бот будет работать на любых серверах")
            return
        for guild in list(bot.guilds):
            await self.leave_if_not_allowed(guild)


guild_handlers = GuildHandlers()
