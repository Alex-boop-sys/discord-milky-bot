"""Ежедневные утренние приветствия Милки."""

import asyncio
import json
import os
import random
from datetime import datetime, time, timedelta, timezone

import discord

from config import paths, var
from src.services.ai import api
from src.utils.json_parser import dicts
from src.utils.logger import log, log_place, place_name
from src.utils.utils import utils


class MorningGreetings:
    def __init__(self):
        self.task = None
        self.state_path = paths.morning_greetings_state
        self._state = self._load_state()

        self.moods = [
            "сонное, но доброе настроение",
            "неожиданно бодрое настроение после пробуждения",
            "уютное и немного ленивое настроение",
            "игривое настроение с лёгкой иронией",
            "спокойное, мечтательное настроение",
            "слегка ворчливое утро, которое всё равно хочется пожелать добрым",
            "довольное настроение после вкусного завтрака",
        ]
        self.scenes = [
            "Милки потягивается после сна и зевает",
            "Милки держит кружку горячего кофе и постепенно просыпается",
            "Милки сонно выглядывает из-под одеяла, хвост тоже торчит",
            "Милки фыркает, расправляет плечи и приводит себя в порядок",
            "Милки принюхивается к завтраку и думает о приятном дне",
            "Милки выглядывает в окно и оценивает погоду за окном, расчёсывает свой белый мех",
            "Милки пытается окончательно проснуться и находит повод улыбнуться",
            "Милки мечтает о чашке чая, фруктах или вкусном завтраке",
            "Милки лениво потягивает лапы и хвост",
        ]
        self.styles = [
            "сделай фразу уютной и тёплой",
            "добавь короткую забавную реплику в характере персонажа",
            "используй лёгкую иронию, но оставайся дружелюбной",
            "можно добавить короткое звукоподражание или действие в звёздочках",
            "напиши живо и естественно, будто это спонтанная реплика в чате",
            "сделай приветствие энергичным, но не чрезмерно восторженным",
        ]
        self.weekday_notes = {
            0: [
                "можно слегка поворчать на понедельник и пожелать пережить его с комфортом",
                "пожелай хорошего начала недели",
            ],
            1: [
                "можно пошутить, что неделя уже набрала ход",
                "пожелать приятного фурри фторника (намеренная забавная опечатка в слове вторник)",
            ],
            2: [
                "можно отметить, что неделя уже добралась до середины",
                "пожелай сил и хорошего настроения в среду",
            ],
            3: [
                "можно намекнуть, что выходные уже виднеются на горизонте",
                "пожелай приятного четверга",
            ],
            4: [
                "порадуйся пятнице и приближению выходных",
                "пожелай бодро дожить до отдыха после рабочей недели",
            ],
            5: [
                "порадуйся субботе и свободному времени",
                "пожелай приятных выходных без лишней суеты",
            ],
            6: [
                "пожелай уютного воскресенья и спокойного отдыха",
                "можно пошутить, что воскресенье стоит растянуть подольше",
            ],
        }

    def _load_state(self):
        try:
            with open(self.state_path, "r", encoding="utf-8") as file:
                state = json.load(file)
                return state if isinstance(state, dict) else {}
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(f"Не удалось прочитать состояние утренних приветствий: {exc}")
            return {}

    def _save_state(self):
        os.makedirs(paths.data, exist_ok=True)
        temporary_path = f"{self.state_path}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as file:
            json.dump(self._state, file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, self.state_path)

    async def start(self, bot):
        """Запускает одну фоновую задачу, в том числе при повторном on_ready."""
        if not var.morning_greetings_enabled:
            return
        if not var.morning_greetings_channel_ids:
            log.warning(
                "GREETINGS_CHANNEL_IDS в .env пуст: утренние приветствия отправляться не будут"
            )
            return
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._scheduler(bot))
            log.info("Планировщик утренних приветствий Милки запущен")

    def _ensure_today_schedule(self, now):
        today = now.date().isoformat()
        if self._state.get("schedule_date") != today:
            # Оставляем запас времени на две повторные попытки до 05:00 UTC.
            latest_second = 55 * 60
            offset = random.randint(0, latest_second)
            scheduled = datetime.combine(
                now.date(),
                time(var.morning_greetings_start_hour_utc, tzinfo=timezone.utc),
            ) + timedelta(seconds=offset)
            self._state = {
                "schedule_date": today,
                "scheduled_at": scheduled.isoformat(),
                "completed_date": self._state.get("completed_date"),
            }
            self._save_state()

    async def _scheduler(self, bot):
        while not bot.is_closed():
            now = datetime.now(timezone.utc)
            self._ensure_today_schedule(now)

            today = now.date().isoformat()
            if self._state.get("completed_date") == today:
                next_midnight = datetime.combine(
                    now.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
                )
                await asyncio.sleep(
                    max(1, min((next_midnight - now).total_seconds(), 300))
                )
                continue

            scheduled_at = datetime.fromisoformat(self._state["scheduled_at"])
            window_end = datetime.combine(
                now.date(),
                time(var.morning_greetings_end_hour_utc, tzinfo=timezone.utc),
            )

            if now >= window_end:
                self._state["completed_date"] = today
                self._save_state()
                log.info(
                    "Утреннее приветствие пропущено: окно 04:00–05:00 UTC закончилось"
                )
                continue

            if now < scheduled_at:
                await asyncio.sleep(
                    max(1, min((scheduled_at - now).total_seconds(), 60))
                )
                continue

            await self._send_greeting(bot, now.date(), window_end)

    def _build_request(self, now):
        weekdays = [
            "понедельник",
            "вторник",
            "среда",
            "четверг",
            "пятница",
            "суббота",
            "воскресенье",
        ]
        weekday = weekdays[now.weekday()]
        mood = random.choice(self.moods)
        scene = random.choice(self.scenes)
        style = random.choice(self.styles)
        weekday_note = random.choice(self.weekday_notes[now.weekday()])
        fragments = [mood, scene, style, weekday_note]
        random.shuffle(fragments)
        details = "; ".join(fragments)
        return (
            f"Сегодня {weekday}, {now:%d.%m.%Y}; текущее время на сервере — "
            f"утро. Напиши короткое утреннее сообщение для общего "
            "Discord-чата от лица Милки: пожелай всем доброго утра и хорошего дня. "
            f"Случайные творческие ориентиры (используй их смысл, но не точный дословный текст!): {details}. "
            "Ответ должен быть только готовым текстом приветствия, без пояснений "
            "и заголовка, обычно 2–3 коротких предложения. Можешь использовать подходящие тематические эмодзи."
        )

    async def _deliver(self, bot, text, scheduled_date):
        """Отправляет один и тот же текст во все каналы из настроек.

        Ошибка в одном канале (удалён, нет прав) не мешает остальным. Каналы, которые
        уже получили приветствие сегодня, пропускаются: так при перезапуске бота
        посреди рассылки никто не получит его дважды.
        Возвращает, в сколько каналов сообщение отправлено сейчас.
        """
        date_key = scheduled_date.isoformat()
        if self._state.get("sent_date") != date_key:
            self._state["sent_date"] = date_key
            self._state["sent_channel_ids"] = []

        sent_now = 0
        for channel_id in var.morning_greetings_channel_ids:
            if channel_id in self._state["sent_channel_ids"]:
                continue

            try:
                channel = bot.get_channel(channel_id)
                if channel is None:
                    channel = await bot.fetch_channel(channel_id)
            except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
                log.warning(f"Утреннее приветствие: канал {channel_id} недоступен: {exc}")
                continue

            guild = getattr(channel, "guild", None)
            with log_place(place_name(guild, channel)):
                if guild is not None and not utils.is_guild_allowed(guild.id):
                    log.warning("Утреннее приветствие пропущено: сервер не в ALLOWED_GUILD_IDS")
                    continue
                try:
                    await channel.send(
                        text, allowed_mentions=discord.AllowedMentions.none()
                    )
                except (discord.HTTPException, discord.Forbidden, discord.NotFound) as exc:
                    log.warning(f"Не удалось отправить утреннее приветствие: {exc}")
                    continue

                self._state["sent_channel_ids"].append(channel_id)
                self._save_state()
                sent_now += 1
                log.info("Утреннее приветствие Милки отправлено")
        return sent_now

    async def _send_greeting(self, bot, scheduled_date, window_end):
        attempts = max(1, var.morning_greetings_attempts)
        greeting = None

        for attempt in range(1, attempts + 1):
            now = datetime.now(timezone.utc)
            if now >= window_end:
                break

            request = self._build_request(now)
            try:
                greeting = await asyncio.wait_for(
                    api.ask_standalone(request, system_prompt=dicts.get("def_prompt")),
                    timeout=var.morning_greetings_request_timeout,
                )
            except asyncio.TimeoutError:
                log.warning(
                    f"Утреннее приветствие: тайм-аут попытки {attempt}/{attempts}"
                )
            except Exception as exc:
                log.warning(
                    f"Утреннее приветствие: ошибка попытки {attempt}/{attempts}: {exc}"
                )

            if greeting and greeting.strip():
                break
            greeting = None
            if attempt < attempts:
                await asyncio.sleep(var.morning_greetings_retry_delay)

        # Не отправляем приветствие за пределами согласованного окна 04:00–05:00 UTC.
        if greeting and datetime.now(timezone.utc) < window_end:
            await self._deliver(bot, greeting.strip(), scheduled_date)
        elif not greeting:
            log.warning(
                "Утреннее приветствие пропущено: все попытки генерации неудачны"
            )
        else:
            log.info("Утреннее приветствие пропущено: окно 04:00–05:00 UTC закончилось")

        self._state["completed_date"] = scheduled_date.isoformat()
        self._save_state()


morning_greetings = MorningGreetings()
