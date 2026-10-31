"""Обработка Discord-событий, относящихся к экономике и реакциям."""

import asyncio
import time

import discord

from src.features.economy import CURRENCY_EMOJI, economy
from src.utils.logger import log, place_name, set_place


class EconomyEventHandler:
    def __init__(self):
        # События обрабатываются по очереди, чтобы две раздачи не шли одновременно.
        self.reaction_event_queue = asyncio.Queue()
        self.reaction_worker_task = None
        self.queued_event_ids = set()

    async def start_reaction_bump_event(self, source_message):
        """Ставит успешный бамп Carl-bot/Discodus в очередь минутных раздач."""
        if not economy.reserve_reaction_event(source_message):
            return
        event = {
            "source_message_id": source_message.id,
            "guild_id": source_message.guild.id,
            "channel_id": source_message.channel.id,
            "reaction_message_id": None,
            "ends_at": None,
        }
        self.queued_event_ids.add(source_message.id)
        await self.reaction_event_queue.put(event)
        self._ensure_reaction_worker()
        log.info(
            f"Бамп поставлен в очередь раздачи Геофруктов "
            f"(сообщение {source_message.id}, бот {source_message.author.id})"
        )

    def _ensure_reaction_worker(self):
        if self.reaction_worker_task is None or self.reaction_worker_task.done():
            self.reaction_worker_task = asyncio.create_task(
                self._reaction_event_worker()
            )

    async def _reaction_event_worker(self):
        """Последовательно проводит раздачи, не допуская параллельных таймеров."""
        from src.bot.bot import bot

        while True:
            event = await self.reaction_event_queue.get()
            source_message_id = event["source_message_id"]
            # Для логов: сервер и канал, где идёт эта раздача.
            set_place(
                place_name(
                    bot.get_guild(event["guild_id"]), bot.get_channel(event["channel_id"])
                )
            )
            try:
                reaction_message_id = event.get("reaction_message_id")
                ends_at = event.get("ends_at")

                if reaction_message_id is None:
                    channel = bot.get_channel(event["channel_id"])
                    if channel is None:
                        channel = await bot.fetch_channel(event["channel_id"])
                    announcement = (
                        "🫐 О-о-о, Милки учуяла свежий бамп! Геофрукты сами в лапки не прыгают!\n"
                        f"Поставь {CURRENCY_EMOJI} на это сообщение в течение минуты — и получишь "
                        "по одному Геофрукту!\n⏰ Осталось: **60 секунд**"
                    )
                    reaction_message = await channel.send(announcement)
                    reaction_message_id = reaction_message.id
                    ends_at = economy.activate_reaction_event(
                        source_message_id, reaction_message_id
                    )
                    try:
                        # Милки ставит первую реакцию, чтобы игрокам оставалось только нажать на неё.
                        await reaction_message.add_reaction(CURRENCY_EMOJI)
                    except discord.HTTPException as exc:
                        log.warning(f"Не удалось добавить начальную реакцию 🫐: {exc}")

                await self._run_reaction_event(
                    source_message_id,
                    event["guild_id"],
                    event["channel_id"],
                    reaction_message_id,
                    ends_at,
                )
            except asyncio.CancelledError:
                raise
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                economy.fail_reaction_event(source_message_id)
                log.error(
                    f"Не удалось провести раздачу Геофруктов {source_message_id}: {exc}"
                )
            except Exception:
                # Любая другая ошибка не должна останавливать обработчик очереди:
                # иначе все следующие раздачи перестали бы проводиться.
                economy.fail_reaction_event(source_message_id)
                log.exception(
                    f"Непредвиденная ошибка раздачи Геофруктов {source_message_id}"
                )
            finally:
                set_place("")
                self.queued_event_ids.discard(source_message_id)
                self.reaction_event_queue.task_done()

    async def _run_reaction_event(
        self, source_message_id, guild_id, channel_id, reaction_message_id, ends_at
    ):
        """Обновляет таймер сообщения и по окончании выдаёт награды участникам."""
        from src.bot.bot import bot

        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)
            reaction_message = await channel.fetch_message(reaction_message_id)

            # Три обновления отсчёта; ждём по абсолютным отметкам времени.
            for remaining in (45, 30, 15):
                milestone = ends_at - remaining
                delay = milestone - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if time.time() >= ends_at:
                    break
                await reaction_message.edit(
                    content=(
                        "🫐 О-о-о, Милки учуяла свежий бамп! Геофрукты сами в лапки не прыгают!\n"
                        f"Поставь {CURRENCY_EMOJI} на это сообщение в течение минуты — и получишь "
                        f"по одному Геофрукту!\n⏰ Осталось: **{remaining} секунд**"
                    )
                )

            delay = ends_at - time.time()
            if delay > 0:
                await asyncio.sleep(delay)

            user_ids = economy.finish_reaction_event(source_message_id)
            if user_ids is None:
                return

            if not user_ids:
                final_text = (
                    "🫐 Время вышло! Никто не успел поймать Геофрукт... "
                    "Ну что ж, Милки забирает его себе. ХРУМ!"
                )
            else:
                mentions = []
                guild = bot.get_guild(guild_id)
                for user_id in user_ids:
                    member = guild.get_member(user_id) if guild else None
                    if member is None and guild:
                        try:
                            member = await guild.fetch_member(user_id)
                        except (
                            discord.NotFound,
                            discord.Forbidden,
                            discord.HTTPException,
                        ):
                            member = None
                    mentions.append(member.mention if member else f"<@{user_id}>")
                winners = ", ".join(mentions)
                final_text = (
                    f"🫐 ХРУМ-ХРУМ! Время вышло — Геофрукты нашли хозяев: {winners}! "
                    "Каждому по одному сочному Геофрукту!"
                )

            await reaction_message.edit(content=final_text)
            log.info(
                f"Завершён сбор реакций за бамп {source_message_id}; "
                f"награждено участников: {len(user_ids)}"
            )
        except asyncio.CancelledError:
            raise
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            economy.fail_reaction_event(source_message_id)
            log.error(f"Ошибка во время события Геофруктов {source_message_id}: {exc}")

    async def on_raw_reaction_add(self, payload):
        """Учитывает добавление 🫐 к активному сообщению-раздаче."""
        from src.bot.bot import bot

        if not economy.enabled_in(payload.guild_id):
            return
        # Для логов: на каком сервере и в каком канале поставили реакцию.
        set_place(
            place_name(bot.get_guild(payload.guild_id), bot.get_channel(payload.channel_id))
        )
        if bot.user and payload.user_id == bot.user.id:
            return
        member = payload.member
        if member is None:
            guild = bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id) if guild else None
        if member and member.bot:
            return
        added = economy.add_reaction_participant(
            payload.message_id,
            payload.user_id,
            payload.guild_id,
            getattr(payload.emoji, "name", None),
        )
        if added:
            log.info(
                f"Участник {payload.user_id} записан в сбор Геофруктов "
                f"(сообщение {payload.message_id})"
            )

    async def resume_reaction_events(self, bot):
        """Восстанавливает активные отсчёты и очередь после перезапуска бота."""
        # Сначала продолжаем уже начатую раздачу, затем запускаем ожидающие.
        for event in economy.get_active_reaction_events():
            source_message_id = event["source_message_id"]
            if source_message_id in self.queued_event_ids:
                continue
            self.queued_event_ids.add(source_message_id)
            await self.reaction_event_queue.put(event)
        for event in economy.get_queued_reaction_events():
            source_message_id = event["source_message_id"]
            if source_message_id in self.queued_event_ids:
                continue
            event["reaction_message_id"] = None
            event["ends_at"] = None
            self.queued_event_ids.add(source_message_id)
            await self.reaction_event_queue.put(event)
        if self.queued_event_ids:
            self._ensure_reaction_worker()


economy_events = EconomyEventHandler()
