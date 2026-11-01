"""Основная обработка входящих сообщений и обращений к Милки.

Путь сообщения:
    on_msg -> проверки (сервер, бамп-боты, игнор-лист)
           -> find_request: обращаются ли к Милки и каков текст запроса
           -> respond: пустой запрос / чужая команда / админ-команда / таймер /
                       слово-команда (арт, генерация) / ответ ИИ
"""

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
from src.utils.triggers import looks_like_foreign_command, match_trigger
from src.utils.utils import utils

# Способы обращения к Милки и как они называются в логе.
REPLY, MENTION, NAME = "reply", "mention", "name"
LOG_LABELS = {REPLY: "reply", MENTION: "сообщение", NAME: "сообщение"}


class MessageHandlers:
    def __init__(self):
        self.economy_events = economy_events
        # Объект бота Discord: нужен админ-командам, чтобы находить каналы на любых
        # серверах. Заполняется при запуске в setup().
        self.client = None

    # ---------------------------------------------------------------- события

    async def on_msg(self, message, bot_user=None):
        # Для логов: на каком сервере и в каком канале всё происходит.
        set_place(place_name(message.guild, message.channel))

        # Страховка: бот ещё мог не успеть выйти с чужого сервера.
        if not utils.is_guild_allowed(message.guild.id if message.guild else None):
            return

        # Бамп-боты проверяем раньше общего фильтра: он отбрасывает всех ботов,
        # а для экономики их подтверждения необходимы.
        await self.handle_economy(message)

        if self.check_ignore(message):
            return

        # В личных сообщениях у message.guild нет значения, поэтому запасной вариант
        # получаем только для серверов.
        bot_user = bot_user or (message.guild.me if message.guild else None)
        if bot_user is None:
            return

        await self.check_ping(message, bot_user)

    async def setup(self, bot):
        """Вызывается один раз при запуске (setup_hook), в отличие от on_ready."""
        self.client = bot
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

    # -------------------------------------------------------------- экономика

    async def handle_economy(self, message):
        if economy.is_reaction_bump_success(message):
            await self.start_reaction_bump_event(message)
            return

        rewarded_user_id = economy.process_bump_message(message)
        if rewarded_user_id is None:
            return

        # Сообщаем о награде в том же канале, где бот подтвердил бамп.
        member = message.guild.get_member(rewarded_user_id) if message.guild else None
        mention = member.mention if member else f"<@{rewarded_user_id}>"
        await message.channel.send(
            f"🫐 Хрум! {mention} получает Геофрукт за успешный бамп! "
            "(Чтобы тоже получить Геофрукт, используй `/bump`.)"
        )

    # События экономики вынесены в отдельный обработчик; эти методы сохраняют
    # прежний интерфейс для остального кода бота.
    async def start_reaction_bump_event(self, source_message):
        await self.economy_events.start_reaction_bump_event(source_message)

    async def on_raw_reaction_add(self, payload):
        await self.economy_events.on_raw_reaction_add(payload)

    async def resume_reaction_events(self, bot):
        await self.economy_events.resume_reaction_events(bot)

    # ------------------------------------------------------ обращения к Милки

    async def check_ping(self, message, bot):
        """Если к Милки обращаются, отвечает; иначе ничего не делает."""
        address = await self.find_request(message, bot)
        if address is None:
            return
        kind, request = address
        try:
            await self.respond(message, kind, request)
        except discord.Forbidden:
            log.warning("Нет прав, чтобы ответить в этом канале")
        except discord.HTTPException as exc:
            log.warning(f"Не удалось ответить: {exc}")

    @staticmethod
    async def find_request(message, bot):
        """Определяет, обращаются ли к Милки, и выделяет текст запроса.

        Возвращает (способ, текст) либо None. Способы проверяются по порядку:
        ответ на сообщение бота, упоминание через @, обращение по имени.
        """
        content = message.content

        # ОТВЕТ НА СООБЩЕНИЕ БОТА
        if message.reference and message.reference.message_id:
            try:
                referenced = await message.channel.fetch_message(
                    message.reference.message_id
                )
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                referenced = None
            if referenced is not None and referenced.author.id == bot.id:
                return REPLY, content.strip()

        # УПОМИНАНИЕ ЧЕРЕЗ @ (учитываем оба формата Discord mention)
        for prefix in (f"<@{bot.id}>", f"<@!{bot.id}>"):
            if content.startswith(prefix):
                return MENTION, MessageHandlers._clean_request(content[len(prefix) :])

        # ПО ИМЕНИ
        lowered = content.lower()
        for name in dicts.get("bot_names", []):
            if lowered.startswith(name):
                return NAME, MessageHandlers._clean_request(content[len(name) :])

        return None

    @staticmethod
    def _clean_request(text):
        """Текст после имени без пробелов по краям и без запятой в начале.

        «Милки, арт лиса» -> «арт лиса». Иначе запятая мешала бы распознать слово-команду
        и попадала бы в запрос к ИИ. Другие знаки («:», «-», «!») не трогаем: они
        важны для правила про чужие команды.
        """
        return text.strip().lstrip(",").strip()

    async def respond(self, message, kind, request):
        """Единый путь ответа для всех способов обращения."""
        # Пустое обращение: просто окликнули (или прислали только картинку).
        if not request:
            key = "image_responses" if message.attachments else "ping_an"
            await message.reply(dicts.get_random(key), mention_author=False)
            return

        # Команды других ботов («!play», ".r d20", «/help») и явное «\» не трогаем.
        # Это раньше таймера, чтобы такие сообщения не тратили его у человека.
        if looks_like_foreign_command(request):
            return

        if await admin_commands.handle(message, request, self.client):
            return

        if not await self._cooldown_ok(message):
            return

        # Слова-команды («арт ...», «сгенерируй ...») работают в прямом обращении;
        # ответ на сообщение бота всегда идёт в ИИ, как и раньше.
        if kind != REPLY and await self.handle_triggers(message, request):
            return

        user_name = message.author.display_name
        async with message.channel.typing():
            response = await api.ask_ai(message.author.id, user_name, request)
            await message.reply(response)
        log.info(f"Ответ на {LOG_LABELS[kind]} {user_name} {message.id} отправлен")

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
    async def handle_triggers(message, request):
        """Слова-команды в тексте: поиск арта и генерация картинки."""
        trigger = match_trigger(request)
        if trigger is None:
            return False
        kind, text = trigger

        # Имя бота внутри самого запроса не нужно.
        for name in dicts.get("bot_names", []):
            text = text.replace(name, "").strip()

        # Запрет NSFW в обычных каналах проверяют сами art_search и image_generation
        # (одна реплика отказа - ключ nsfw_rejection в dicts.json).
        async with message.channel.typing():
            if kind == "art":
                await art_search.art(message, text or None)
            else:
                await image_generation.generate(message, text or None)
        return True

    # ------------------------------------------------------------ игнор-лист

    @staticmethod
    def check_ignore(message):
        """True, если сообщение нужно пропустить: бот или пользователь из игнор-листа."""
        if message.author.bot:  # в том числе сам Милки
            return True

        entry = ignored_users.get("ignored_users", {}).get(str(message.author.id))
        if entry is None or MessageHandlers._ignore_expired(entry):
            return False

        log.info(f"Попытка обращения игнорируемого пользователя {message.author.id}")
        return True

    @staticmethod
    def _ignore_expired(entry):
        """Истёк ли срок игнорирования. Записи без срока бессрочные."""
        expires_at = entry.get("expires_at")
        if not expires_at:
            return False
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError:
            log.warning(f"Некорректная дата expires_at в ignored_users.json: {expires_at!r}")
            return False  # лучше оставить игнор, чем случайно снять его
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires


handlers = MessageHandlers()
