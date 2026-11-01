"""Административные команды Милки для владельцев бота (root# ...).

Команды работают только у владельцев (OWNER_ID, CO_OWNER_ID в .env) и только когда
к Милки обратились (по имени, через @ или ответом на её сообщение), например:
    Милки root# say 123456789012345678 Всем привет!

Результат команд, которые что-то делают, показывается реакцией на сообщение с
командой: ✅ получилось, ❌ не получилось (причина пишется в лог, её видно в root# log).
При неверном формате бот отвечает правильным форматом. Информационные ответы
(log, bans, servers, status, ai) бот сам удаляет через минуту.
"""

import math
import os
import platform
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import discord

from config import VERSION, env, paths, var
from src.services.ai import api
from src.services.history import history
from src.services.ignore_list import ignore_list, parse_time
from src.utils.logger import log, place_name
from src.utils.utils import utils

ADMIN_TRIGGER = "root#"

OK_MARK, FAIL_MARK, LOG_MARK = "✅", "❌", "📩"
DISCORD_LIMIT = 2000   # максимум символов в сообщении Discord
LOG_LINES = 10         # сколько последних строк лога показывает root# log
INFO_LIFETIME = 60     # через сколько секунд бот сам удаляет информационные ответы
MAX_BAN_HOURS = 24 * 365 * 10   # дольше 10 лет игнорировать нет смысла
STARTED_AT = time.monotonic()   # момент запуска программы (для аптайма в status)
NO_MENTIONS = discord.AllowedMentions.none()   # ники и причины никого не пингуют

HELP_TEXT = (
    "**Административные команды Милки**\n"
    "`root# ver` — версия программы\n"
    "`root# log` — последние 10 строк лога\n"
    "`root# ban ид [срок] [причина]` — игнорировать человека (срок: `24`, `24ч`, `7д`; без срока — бессрочно)\n"
    "`root# unban ид` — снова отвечать человеку\n"
    "`root# bans` — список игнорируемых\n"
    "`root# servers` — серверы, где есть Милки\n"
    "`root# status` — состояние бота\n"
    "`root# ai` — проверить ИИ-провайдеров\n"
    "`root# say [ид_сервера] ид_канала текст` — написать от имени Милки в канал\n"
    "`root# reply [[ид_сервера] ид_канала] ид_сообщения текст` — ответить на сообщение\n"
    "`root# reaction [[ид_сервера] ид_канала] ид_сообщения эмодзи` — поставить реакцию\n"
    "`root# del [[ид_сервера] ид_канала] ид_сообщения` — удалить сообщение Милки\n"
    "`root# help` — эта справка\n\n"
    "Если канал не указан, берётся текущий. Вместо чисел можно писать `<#канал>`, `<@человек>` "
    "или вставить ссылку на сообщение. Итог команды — реакция: ✅ или ❌. "
    "Списки и отчёты исчезают сами через минуту."
)

USAGE = {
    "ban": (
        "`root# ban ид [срок] [причина]`\n"
        "Срок: `24` или `24ч` — часы, `7д` — дни; без срока — бессрочно.\n"
        "Например: `root# ban 123456789012345678 7д спам в чате`"
    ),
    "unban": (
        "`root# unban ид`\n"
        "Например: `root# unban 123456789012345678`"
    ),
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


USER_RE = re.compile(rf"\s*(?:<@!?(?P<mention>{SNOWFLAKE})>|(?P<id>{SNOWFLAKE}))(?=\s|$)")
DURATION_RE = re.compile(r"(\d+)([чhдd]?)(?=\s|$)", re.IGNORECASE)


def split_user(args):
    """Читает с начала строки пользователя (ID или упоминание). Возвращает (ID, остаток) или None."""
    match = USER_RE.match(args)
    if match is None:
        return None
    return int(match["mention"] or match["id"]), args[match.end() :].strip()


def split_duration(rest):
    """Необязательный срок в начале: `24`/`24ч` - часы, `7д` - дни.

    Возвращает (часы или None, остаток - то есть причина). Срок должен быть больше нуля.
    """
    match = DURATION_RE.match(rest)
    if match is None:
        return None, rest
    hours = int(match[1]) * (24 if match[2].lower() in ("д", "d") else 1)
    if not 0 < hours <= MAX_BAN_HOURS:
        raise UsageError
    return hours, rest[match.end() :].strip()


def format_duration(seconds):
    """Секунды -> «2 д 3 ч 5 мин»; меньше минуты - «42 с»."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    days, minutes = divmod(seconds // 60, 60 * 24)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes or not parts:
        parts.append(f"{minutes} мин")
    return " ".join(parts)


def format_size(num_bytes):
    if num_bytes < 1024:
        return f"{num_bytes} Б"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} КБ"
    return f"{num_bytes / 1024 / 1024:.1f} МБ"


def fit_lines(header, lines):
    """Собирает сообщение из заголовка и строк так, чтобы оно влезло в лимит Discord."""
    budget = DISCORD_LIMIT - 30   # запас под строку «…и ещё N»
    shown, used = [], len(header)
    for line in lines:
        if used + len(line) + 1 > budget:
            break
        shown.append(line)
        used += len(line) + 1
    text = header + "\n".join(shown)
    if len(shown) < len(lines):
        text += f"\n…и ещё {len(lines) - len(shown)}"
    return text


def short(text, limit=80):
    """Укороченный текст для строки лога."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class AdminCommands:
    def __init__(self):
        self._commands = {
            "ver": self._cmd_ver,
            "log": self._cmd_log,
            "ban": self._cmd_ban,
            "unban": self._cmd_unban,
            "bans": self._cmd_bans,
            "servers": self._cmd_servers,
            "status": self._cmd_status,
            "ai": self._cmd_ai,
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
            f"```\n{text}\n```", mention_author=False, delete_after=INFO_LIFETIME
        )
        return LOG_MARK

    async def _cmd_ban(self, message, args, client):
        parsed = split_user(args)
        if parsed is None:
            raise UsageError
        user_id, rest = parsed
        hours, reason = split_duration(rest)

        if user_id in env.owner_ids or (client is not None and user_id == client.user.id):
            raise AdminError("нельзя игнорировать владельца бота или саму Милки")
        user = await self._lookup_user(client, user_id)   # заодно ловит опечатки в ID
        if user.bot:
            raise AdminError("это бот, боты и так игнорируются")
        nickname = getattr(user, "global_name", None) or user.name

        try:
            entry = ignore_list.add(user_id, nickname, hours, reason, banned_by=message.author.id)
        except OSError as exc:
            raise AdminError(f"не удалось сохранить список игнорируемых: {exc}") from exc

        until = self._until_text(entry)
        await message.reply(
            f"🔇 Милки теперь игнорирует **{discord.utils.escape_markdown(nickname)}** "
            f"(`{user_id}`), {until}.",
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )
        log.info(
            f"root# ban от {message.author.id}: {user_id} ({nickname}), {until}; "
            f"причина: {short(reason) or '-'}"
        )
        return OK_MARK

    async def _cmd_unban(self, message, args, client):
        parsed = split_user(args)
        if parsed is None or parsed[1]:
            raise UsageError
        user_id = parsed[0]
        try:
            entry = ignore_list.remove(user_id)
        except OSError as exc:
            raise AdminError(f"не удалось сохранить список игнорируемых: {exc}") from exc
        if entry is None:
            raise AdminError(f"{user_id} нет в списке игнорируемых")

        nickname = entry.get("nickname") or str(user_id)
        await message.reply(
            f"🔔 Милки снова отвечает **{discord.utils.escape_markdown(nickname)}** (`{user_id}`).",
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )
        log.info(f"root# unban от {message.author.id}: {user_id} ({nickname})")
        return OK_MARK

    async def _cmd_bans(self, message, args, client):
        items = ignore_list.active()
        if not items:
            text = "Список игнорируемых пуст."
        else:
            lines = []
            for user_id, entry in items:
                nickname = discord.utils.escape_markdown(entry.get("nickname") or "?")
                line = f"• **{nickname}** (`{user_id}`) — {self._until_text(entry, remaining=True)}"
                if entry.get("reason"):
                    line += f" — {short(entry['reason'], 100)}"
                lines.append(line)
            text = fit_lines(f"**Игнорируемые ({len(items)})**\n", lines)
        await self._info_reply(message, text)

    async def _cmd_servers(self, message, args, client):
        if client is None:
            raise AdminError("клиент Discord недоступен")
        lines = []
        for guild in sorted(client.guilds, key=lambda g: g.name.lower()):
            tags = []
            if guild.id == var.main_guild_id:
                tags.append("🏠")
            if guild.id in var.economy_guild_ids:
                tags.append("🫐")
            if not utils.is_guild_allowed(guild.id):
                tags.append("⚠️")
            members = guild.member_count if guild.member_count is not None else "?"
            name = discord.utils.escape_markdown(guild.name)
            lines.append(f"• **{name}** (`{guild.id}`) — {members} уч. {' '.join(tags)}".rstrip())
        header = (
            f"**Серверы Милки ({len(lines)})**\n"
            "🏠 основной · 🫐 экономика · ⚠️ нет в ALLOWED_GUILD_IDS\n"
        )
        await self._info_reply(message, fit_lines(header, lines))

    async def _cmd_status(self, message, args, client):
        latency = getattr(client, "latency", None)
        ping = f"{latency * 1000:.0f} мс" if latency is not None and math.isfinite(latency) else "—"
        guilds = list(getattr(client, "guilds", []))
        members = sum(g.member_count or 0 for g in guilds)
        summary = history.get_summary()
        lines = [
            "**Состояние Милки**",
            f"Версия `{VERSION}` · Python {platform.python_version()} · discord.py {discord.__version__}",
            f"Работает: {format_duration(time.monotonic() - STARTED_AT)} · задержка до Discord: {ping}",
            f"Серверов: {len(guilds)} (участников: {members})",
            f"История: {summary['history_rows']} записей · статистика: {summary['users']} чел., "
            f"{summary['total_messages']} обращений",
            f"Базы: history.db {self._file_size(paths.db_path)} · economy.db {self._file_size(paths.economy_db)}",
            f"Игнорируется: {len(ignore_list.active())} · таймер: {var.user_cooldown_seconds} с · "
            f"контекст: {var.max_history} записей",
            f"Приветствия: каналов {len(var.morning_greetings_channel_ids)} · "
            f"экономика: серверов {len(var.economy_guild_ids)}",
        ]
        await self._info_reply(message, "\n".join(lines))

    async def _cmd_ai(self, message, args, client):
        async with message.channel.typing():
            results = await api.check_providers()

        lines = []
        for check in results:
            model = check.model.split("/")[-1]
            if check.status == "ok":
                detail = f"✅ {check.seconds:.1f} с"
            elif check.status == "no_key":
                detail = "⚪ ключ не задан в .env"
            else:
                limit = getattr(var, f"{check.provider}_timeout", None)
                if limit and check.seconds >= limit - 0.5:
                    detail = f"❌ таймаут ({limit} с)"
                else:
                    detail = "❌ нет ответа (причина в логе: `root# log`)"
            lines.append(f"• **{check.provider}** · {model} — {detail}")
        working = sum(1 for check in results if check.status == "ok")
        header = (
            f"**Проверка ИИ-провайдеров** (отвечают: {working} из {len(results)})\n"
            "Опрашиваются по порядку цепочки: пользователям отвечает первый работающий.\n"
        )
        await self._info_reply(message, fit_lines(header, lines))

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
    async def _lookup_user(client, user_id):
        """Находит пользователя Discord по ID (в кэше или через API)."""
        if client is None:
            raise AdminError("клиент Discord недоступен")
        user = client.get_user(user_id)
        if user is None:
            try:
                user = await client.fetch_user(user_id)
            except discord.NotFound:
                raise AdminError(f"пользователь {user_id} не найден") from None
        return user

    @staticmethod
    def _until_text(entry, remaining=False):
        """«бессрочно» или «до 05.11.2026 14:30 UTC» (с остатком, если remaining=True)."""
        expires = parse_time(entry.get("expires_at"))
        if expires is None:
            return "бессрочно"
        text = f"до {expires.strftime('%d.%m.%Y %H:%M')} UTC"
        if remaining:
            left = (expires - datetime.now(timezone.utc)).total_seconds()
            text += f" (ещё {format_duration(max(left, 0))})"
        return text

    @staticmethod
    def _file_size(path):
        try:
            return format_size(os.path.getsize(path))
        except OSError:
            return "нет файла"

    @staticmethod
    async def _info_reply(message, text):
        """Информационный ответ: без пингов, сам исчезает через минуту."""
        await message.reply(
            text,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
            delete_after=INFO_LIFETIME,
        )

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
