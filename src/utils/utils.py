# src/utils/utils.py
import re
import sys

from config import env, var
from src.utils.logger import log

# Подпись для участников, которых нет на текущем сервере (например, в общей статистике).
HIDDEN_USER_NAME = "Пользователь скрыт"


class Utils:
    @staticmethod
    def exit(text, code=0):
        if code == 1:
            log.fatal(text)
            log.info("Завершение работы из-за критической ошибки")
        elif code == 0:
            log.info(text)
        sys.exit(code)

    @staticmethod
    def get_size_limit(guild):
        if guild is None:
            return 8 * 1024 * 1024
        return var.limits.get(guild.premium_tier, 8 * 1024 * 1024)

    @staticmethod
    def is_guild_allowed(guild_id):
        """Можно ли боту работать на этом сервере (список ALLOWED_GUILD_IDS в config.py).

        Личные сообщения (guild_id=None) разрешены. Пустой список = без ограничений.
        """
        if guild_id is None or not var.allowed_guild_ids:
            return True
        return guild_id in var.allowed_guild_ids

    @staticmethod
    def member_display_name(guild, user_id):
        """Имя участника этого сервера, а если его здесь нет - «Пользователь скрыт»."""
        member = guild.get_member(user_id) if guild is not None else None
        return member.display_name if member is not None else HIDDEN_USER_NAME

    @staticmethod
    def is_nsfw_channel(channel):
        """True только для NSFW-каналов. В ЛС и если канал неизвестен возвращает False."""
        check = getattr(channel, "is_nsfw", None)
        return bool(check()) if check else False

    def check_tokens(self):
        # ID владельцев необязательны: без них бот работает, но admin-команды
        # будут недоступны. Остальные настройки сохраняют прежнюю проверку.
        optional_names = {"owner_id", "co_owner_id"}
        for name, value in env:
            if name in optional_names:
                continue
            if value is None:
                self.exit(f"Токен/настройка {name} не найдена!", 1)

    @staticmethod
    def have_nsfw(text):
        from src.utils.json_parser import dicts

        text_lower = text.lower()
        for pattern in dicts.nsfw_patterns:
            if re.search(pattern, text_lower):
                return True
        return False


utils = Utils()
