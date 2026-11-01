"""Административные команды Милки для владельцев бота (root# ...).

Команды работают только у владельцев (OWNER_ID, CO_OWNER_ID в .env) и только когда
к Милки обратились (по имени, через @ или ответом на её сообщение), например:
    Милки root# say 123456789012345678 Всем привет!

Результат команд, которые что-то делают, показывается реакцией на сообщение с
командой: ✅ получилось, ❌ не получилось (причина пишется в лог, её видно в root# log).
При неверном формате бот отвечает правильным форматом.
"""

import re
from collections import deque
from pathlib import Path

import discord

from config import VERSION, env, paths
from src.utils.logger import log, place_name

ADMIN_TRIGGER = "root#"

OK_MARK, FAIL_MARK, LOG_MARK = "✅", "❌", "📩"
DISCORD_LIMIT = 2000   # максимум символов в сообщении Discord
LOG_LINES = 10         # сколько последних строк лога показывает root# log
LOG_LIFETIME = 60      # через сколько секунд бот сам удаляет сообщение с логом

HELP_TEXT = (
    "**Административные команды Милки**\n"
    "`root# ver` — версия программы\n"
    "`root# log` — последние 10 строк лога (сообщение само исчезнет через минуту)\n"
    "`root# say [ид_сервера] ид_канала текст` — написать от имени Милки в канал\n"
    "`root# reply [[ид_сервера] ид_канала] ид_сообщения текст` — ответить на сообщение\n"
    "`root# reaction [[ид_сервера] ид_канала] ид_сообщения эмодзи` — поставить реакцию\n"
    "`root# del [[ид_сервера] ид_канала] ид_сообщения` — удалить сообщение Милки\n"
    "`root# help` — эта справка\n\n"
    "Если канал не указан, берётся текущий. Вместо чисел можно писать `<#канал>` или "
    "вставить ссылку на сообщение. Итог команды — реакция на ваше сообщение: ✅ или ❌."
)

USAGE = {
    "say": (
        "`root# say [ид_сервера] ид_канала текст`\n"
        "Например: `root# say 123456789012345678 Всем привет!`"
    ),
    "reply": (
        "`root# reply [[ид_сервера] ид_канала] ид_сообщения текст`\n"
        "Например: `root# reply 123456789012345678 Согласна!`"
    ),
    "reaction": (
        "`root# reaction [[ид_сервера] ид_канала] ид_сообщения эмодзи`\n"
        "Например: `root# reaction 123456789012345678 👍`"
    ),
    "del": (
        "`root# del [[ид_сервера] ид_канала] ид_сообщения`\n"
        "Например: `root# del 123456789012345678`"
    ),
}

# ID в Discord - числа из 17-20 цифр; более короткие числа считаются обычным текстом.
SNOWFLAKE = r"\d{17,20}"
TOKEN_RE = re.compile(
    rf"\s*(?:"
    rf"https?://(?:\w+\.)?discord(?:app)?\.com/channels/(?P<link_guild>{SNOWFLAKE}|@me)"
    rf"/(?P<link_channel>{SNOWFLAKE})(?:/(?P<link_message>{SNOWFLAKE}))?"
    rf"|<#(?P<mention>{SNOWFLAKE})>"
    rf"|(?P<id>{SNOWFLAKE})"
    rf")(?=\s|$)"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class UsageError(Exception):
    """Команда написана в неверном формате: покажем правильный."""


class AdminError(Exception):
    """Команда написана верно, но выполнить её не вышло (причина пойдёт в лог)."""


def split_ids(args, limit):
    """Читает с начала строки до `limit` ID: число, `<#канал>` или ссылку на сообщение.

    Возвращает (список ID, остаток строки). Ссылка вида .../сервер/канал/сообщение
    даёт сразу три ID. Остаток - это текст команды (текст для say/reply, эмодзи).
    """
    ids = []
    rest = args
    while len(ids) < limit:
        match = TOKEN_RE.match(rest)
        if match is None:
            break
        if match["link_guild"]:
            if match["link_guild"] == "@me":
                raise AdminError("ссылки на личные сообщения не поддерживаются")
            found = [int(match["link_guild"]), int(match["link_channel"])]
            if match["link_message"]:
                found.append(int(match["link_message"]))
        elif match["mention"]:
            found = [int(match["mention"])]
        else:
            found = [int(match["id"])]
        if len(ids) + len(found) > limit:
            break
        ids.extend(found)
        rest = rest[match.end() :]
    return ids, rest.strip()


def short(text, limit=80):
    """Укороченный текст для строки лога."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AdminCommands:
    def __init__(self):
        self._commands = {
            "ver": self._cmd_ver,
            "log": self._cmd_log,
            "say": self._cmd_say,
            "reply": self._cmd_reply,
            "reaction": self._cmd_reaction,
            "del": self._cmd_del,
        }

    async def handle(self, message, request: str, client=None) -> bool:
        """Обрабатывает команду, если обращение к боту уже подтверждено.

        Возвращает True, если сообщение начинается с административного триггера.
        `client` - объект бота Discord (нужен, чтобы находить каналы на любых серверах).
        """
        cleaned = request.strip()
        if not cleaned.lower().startswith(ADMIN_TRIGGER):
            return False

        # Название команды не зависит от регистра, а текст после него остаётся как есть.
        parts = cleaned[len(ADMIN_TRIGGER) :].split(maxsplit=1)
        command = parts[0].lower() if parts else "help"
        args = parts[1] if len(parts) > 1 else ""

        if message.author.id not in env.owner_ids:
            await message.reply(
                "⛔ Эта административная команда тебе недоступна.",
                mention_author=False,
            )
            log.warning(
                "Неавторизованная попытка выполнить root# от пользователя %s",
                message.author.id,
            )
            return True

        action = self._commands.get(command)
        if action is None:
            # help и любые неизвестные команды возвращают справку.
            await message.reply(HELP_TEXT, mention_author=False)
            return True

        try:
            mark = await action(message, args, client)
        except UsageError:
            await message.reply(
                f"Неверный формат команды. Правильно:\n{USAGE[command]}",
                mention_author=False,
            )
            mark = FAIL_MARK
        except AdminError as exc:
            log.warning(f"root# {command} не выполнена: {exc}")
            mark = FAIL_MARK
        except discord.Forbidden:
            log.warning(f"root# {command} не выполнена: у бота нет нужных прав")
            mark = FAIL_MARK
        except discord.HTTPException as exc:
            log.warning(f"root# {command} не выполнена: {exc}")
            mark = FAIL_MARK

        if mark:
            await self._mark(message, mark)
        return True

    # ------------------------------------------------------------------ команды
    # Каждая команда возвращает реакцию-итог для сообщения с командой (или None).

    async def _cmd_ver(self, message, args, client):
        await message.reply(
            f"Милки работает на версии `{VERSION}`.", mention_author=False
        )

    async def _cmd_log(self, message, args, client):
        text = self._read_log_tail()
        await message.reply(
            f"```\n{text}\n```", mention_author=False, delete_after=LOG_LIFETIME
        )
        return LOG_MARK

    async def _cmd_say(self, message, args, client):
        ids, text = split_ids(args, limit=2)
        if not ids or not text:
            raise UsageError
        guild_id, channel_id = (None, ids[0]) if len(ids) == 1 else (ids[0], ids[1])
        self._check_length(text)

        channel = await self._find_channel(client, guild_id, channel_id)
        await channel.send(text)
        log.info(
            f"root# say от {message.author.id} -> "
            f"{place_name(getattr(channel, 'guild', None), channel)}: {short(text)}"
        )
        return OK_MARK

    async def _cmd_reply(self, message, args, client):
        ids, text = split_ids(args, limit=3)
        if not ids or not text:
            raise UsageError
        self._check_length(text)

        channel, target = await self._find_message(message, client, ids)
        await target.reply(text)
        log.info(
            f"root# reply от {message.author.id} -> сообщение {target.id} в "
            f"{place_name(getattr(channel, 'guild', None), channel)}: {short(text)}"
        )
        return OK_MARK

    async def _cmd_reaction(self, message, args, client):
        ids, emoji = split_ids(args, limit=3)
        # Эмодзи ровно одно: обычное (👍) или серверное (<:имя:id>).
        if not ids or not emoji or len(emoji.split()) != 1:
            raise UsageError

        channel, target = await self._find_message(message, client, ids)
        # PartialEmoji сам разбирает и обычные эмодзи, и серверные/анимированные.
        await target.add_reaction(discord.PartialEmoji.from_str(emoji))
        log.info(
            f"root# reaction от {message.author.id} -> сообщение {target.id} в "
            f"{place_name(getattr(channel, 'guild', None), channel)}: {emoji}"
        )
        return OK_MARK

    async def _cmd_del(self, message, args, client):
        ids, rest = split_ids(args, limit=3)
        if not ids or rest:
            raise UsageError

        channel, target = await self._find_message(message, client, ids)
        # Удаляем только собственные сообщения Милки, даже если прав у бота больше.
        if client is None or target.author.id != client.user.id:
            raise AdminError(f"сообщение {target.id} написано не Милки, удалять его нельзя")
        await target.delete()
        log.info(
            f"root# del от {message.author.id} -> сообщение {target.id} в "
            f"{place_name(getattr(channel, 'guild', None), channel)}"
        )
        return OK_MARK

    # ------------------------------------------------------------- вспомогательное

    @staticmethod
    def _check_length(text):
        if len(text) > DISCORD_LIMIT:
            raise AdminError(f"текст длиннее {DISCORD_LIMIT} символов ({len(text)})")

    @staticmethod
    async def _find_channel(client, guild_id, channel_id):
        """Находит канал по ID; если указан сервер, канал должен быть именно на нём."""
        if client is None:
            raise AdminError("клиент Discord недоступен")

        if guild_id is None:
            channel = client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await client.fetch_channel(channel_id)
                except discord.NotFound:
                    raise AdminError(f"канал {channel_id} не найден") from None
        else:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise AdminError(f"Милки нет на сервере {guild_id}")
            channel = guild.get_channel_or_thread(channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(channel_id)
                except discord.NotFound:
                    raise AdminError(
                        f"на сервере {guild_id} нет канала {channel_id}"
                    ) from None

        if not hasattr(channel, "send"):
            raise AdminError(f"в канал {channel_id} нельзя писать сообщения")
        return channel

    async def _find_message(self, message, client, ids):
        """По списку ID ([сервер] [канал] сообщение) находит канал и само сообщение."""
        guild_id, channel_id, message_id = ([None, None] + ids)[-3:]
        if channel_id is None:
            channel = message.channel  # канал не указан - берём тот, где написана команда
        else:
            channel = await self._find_channel(client, guild_id, channel_id)
        try:
            return channel, await channel.fetch_message(message_id)
        except discord.NotFound:
            raise AdminError(f"сообщение {message_id} не найдено") from None

    @staticmethod
    async def _mark(message, emoji):
        """Ставит итоговую реакцию на сообщение с командой."""
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException as exc:
            log.warning(f"Не удалось поставить реакцию {emoji} на команду: {exc}")

    @staticmethod
    def _read_log_tail():
        """Последние строки лога в таком виде, чтобы они гарантированно влезли в сообщение."""
        log_path = Path(paths.logs) / "milky.log"
        if not log_path.is_file():
            return "Лог пока пуст."
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as file:
                lines = deque(file, maxlen=LOG_LINES)
        except OSError as exc:
            raise AdminError(f"не удалось прочитать лог: {exc}") from exc

        text = ANSI_RE.sub("", "".join(lines)).strip() or "Лог пока пуст."
        text = text.replace("```", "'''")  # иначе строка лога сломает блок кода
        room = DISCORD_LIMIT - len("```\n\n```")
        if len(text) > room:
            text = "…" + text[-(room - 1) :]  # самое свежее важнее: режем с начала
        return text


admin_commands = AdminCommands()
