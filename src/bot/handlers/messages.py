"""Основная обработка входящих сообщений и обращений к Милки."""

import math
from datetime import datetime, timezone

import discord

from src.bot.admin_commands import admin_commands
from src.bot.handlers.economy import economy_events
from src.bot.handlers.guilds import guild_handlers
from src.features.art_search import art_search
from src.features.economy import economy
from src.features.image_generation import image_generation
from src.features.morning_greetings import morning_greetings
from src.services.ai import api
from src.utils.cooldown import cooldown
from src.utils.json_parser import dicts, ignored_users
from src.utils.logger import log, place_name, set_place
from src.utils.triggers import match_trigger
from src.utils.utils import utils


class MessageHandlers:
    def __init__(self):
        self.economy_events = economy_events

    async def on_msg(self, message):
        # Для логов: на каком сервере и в каком канале всё происходит.
        set_place(place_name(message.guild, message.channel))

        # Страховка: бот ещё мог не успеть выйти с чужого сервера.
        if not utils.is_guild_allowed(message.guild.id if message.guild else None):
            return

        # Сначала проверяем подтверждения бамп-ботов: обычный фильтр ниже
        # игнорирует сообщения от ботов, а для экономики они необходимы.
        if economy.is_reaction_bump_success(message):
            await self.start_reaction_bump_event(message)
        else:
            rewarded_user_id = economy.process_bump_message(message)
            if rewarded_user_id is not None:
                # Сообщаем о награде в том же канале, где бот подтвердил бамп.
                member = (
                    message.guild.get_member(rewarded_user_id)
                    if message.guild
                    else None
                )
                mention = member.mention if member else f"<@{rewarded_user_id}>"
                await message.channel.send(
                    f"🫐 Хрум! {mention} получает Геофрукт за успешный бамп! "
                    "(Чтобы тоже получить Геофрукт, используй `/bump`.)"
                )

        if await self.check_ignore(message):
            return

        # Получаем именно текущего бота. В личных сообщениях у message.guild нет
        # значения, поэтому message.author нельзя использовать как автора-бота.
        from src.bot.bot import bot as discord_bot

        bot_user = discord_bot.user
        if bot_user is None and message.guild:
            bot_user = message.guild.me
        if bot_user is None:
            return

        # await discord_bot.process_commands(message)
        await self.check_ping(message, bot_user)

    async def setup(self, bot):
        """Вызывается один раз при запуске (setup_hook), в отличие от on_ready."""
        try:
            synced = await bot.tree.sync()
            log.info(f"Синхронизировано {len(synced)} команд")
        except Exception as e:
            log.error(f"Ошибка синхронизации команд: {e}")

    async def on_ready(self, bot):
        log.info(f"Бот запущен ({bot.user})")
        await guild_handlers.sweep(bot)
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="Фырк 🐺"
            )
        )
        await self.resume_reaction_events(bot)
        await morning_greetings.start(bot)

    async def check_ping(self, message, bot):
        content = message.content
        user_name = message.author.display_name

        # ОТВЕТ НА СООБЩЕНИЕ
        if message.reference and message.reference.message_id:
            try:
                referenced = await message.channel.fetch_message(
                    message.reference.message_id
                )
                # ПРОВЕРКА АВТОРА
                if referenced.author.id == bot.id:
                    request = content.strip()
                    if not request and message.attachments:
                        await message.reply(
                            dicts.get_random("image_responses"), mention_author=False
                        )
                        return
                    if not request:
                        await message.reply(
                            dicts.get_random("ping_an"), mention_author=False
                        )
                        return
                    if await admin_commands.handle(message, request):
                        return
                    if request.startswith((".", "/", "-", "!", ":", ";")):
                        return
                    if not await self._cooldown_ok(message):
                        return
                    async with message.channel.typing():
                        response = await api.ask_ai(
                            message.author.id, user_name, request
                        )
                        await message.reply(response)
                        log.info(f"Ответ на reply {user_name} {message.id} отправлен")
                    return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # УПОМИНАНИЕ ЧЕРЕЗ @ (учитываем оба формата Discord mention).
        mention_prefix = next(
            (
                prefix
                for prefix in (f"<@{bot.id}>", f"<@!{bot.id}>")
                if content.startswith(prefix)
            ),
            None,
        )
        if mention_prefix:
            request = content[len(mention_prefix) :].strip()
            if not request:
                await message.reply(dicts.get_random("ping_an"), mention_author=False)
                return

            if await admin_commands.handle(message, request):
                return

            if request.startswith((".", "/", "-", "!", ":", ";")):
                return

            if not await self._cooldown_ok(message):
                return

            if await self.handle_triggers(message, request, bot):
                return

            async with message.channel.typing():
                response = await api.ask_ai(message.author.id, user_name, request)
                await message.reply(response)
                log.info(f"Ответ на сообщение {user_name} {message.id} отправлен")
            return

        # ПО ИМЕНИ
        for name in dicts.get("bot_names", []):
            if content.lower().startswith(name):
                request = content[len(name) :].strip()
                if not request:
                    await message.reply(
                        dicts.get_random("ping_an"), mention_author=False
                    )
                    return

                if await admin_commands.handle(message, request):
                    return

                if request.startswith((".", "/", "-", "!", ":", ";")):
                    return

                if not await self._cooldown_ok(message):
                    return

                if await self.handle_triggers(message, request, bot):
                    return

                async with message.channel.typing():
                    response = await api.ask_ai(message.author.id, user_name, request)
                    await message.reply(response)
                    log.info(f"Ответ на сообщение {user_name} {message.id} отправлен")
                return

    @staticmethod
    async def _cooldown_ok(message):
        """False, если человек обращается слишком часто (таймер в config.py).

        Предупреждение пишем один раз за таймер и сами стираем его через 5 секунд.
        """
        allowed, remaining, notify = cooldown.check(message.author.id)
        if not allowed and notify:
            try:
                await message.reply(
                    dicts.get("cooldown_notice").format(seconds=math.ceil(remaining)),
                    mention_author=False,
                    delete_after=5,
                )
            except discord.HTTPException:
                pass
        return allowed

    @staticmethod
    async def handle_triggers(message, request, bot):
        trigger = match_trigger(request)
        if trigger is None:
            return False
        kind, text = trigger

        # Имя бота внутри самого запроса не нужно.
        for name in dicts.get("bot_names", []):
            text = text.replace(name, "").strip()

        async with message.channel.typing():
            if utils.have_nsfw(text) and not utils.is_nsfw_channel(message.channel):
                if kind == "art":
                    reply = "\\*Фыркает\\* NSFW контент доступен только в каналах с возрастным ограничением"
                else:
                    reply = "\\*Поджимает уши\\* NSFW контент доступен только в каналах с возрастным ограничением"
                await message.reply(reply, mention_author=False)
                return True

            if kind == "art":
                await art_search.art(message, text or None)
            else:
                await image_generation.generate(message, text or None)
        return True

    @staticmethod
    async def check_ignore(message):
        if message.author.bot:
            return True

        if message.guild and message.author.id == message.guild.me.id:
            return True

        user_id = str(message.author.id)
        ign_users = ignored_users.get("ignored_users", {})
        if user_id in ign_users:
            expires_at_str = ign_users[user_id].get("expires_at")

            if expires_at_str:
                expire_datetime = datetime.fromisoformat(
                    expires_at_str.replace("Z", "+00:00")
                )
                now = datetime.now(timezone.utc)
                if now >= expire_datetime:
                    return False
            log.info(
                f"Попытка обращения игнорируемого пользователя {message.author.id}"
            )
            return True

        return False

    @staticmethod
    async def check_message(message):
        pass

    # События экономики вынесены в отдельный обработчик; эти методы сохраняют
    # прежний интерфейс для остального кода бота.
    async def start_reaction_bump_event(self, source_message):
        await self.economy_events.start_reaction_bump_event(source_message)

    async def on_raw_reaction_add(self, payload):
        await self.economy_events.on_raw_reaction_add(payload)

    async def resume_reaction_events(self, bot):
        await self.economy_events.resume_reaction_events(bot)


handlers = MessageHandlers()
