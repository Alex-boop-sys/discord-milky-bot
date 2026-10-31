# src/features/discord_actions.py
import io
import math
import random
import urllib.parse

import aiohttp
import discord

from src.features.art_search import art_search
from src.features.image_generation import image_generation
from src.services.ai import api
from src.utils.cooldown import cooldown
from src.utils.json_parser import dicts
from src.utils.logger import log
from src.utils.utils import utils


class Defs:
    @staticmethod
    async def fetch_image(session, url, timeout=20):
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None, resp.status
            data = await resp.read()
            return data, None

    @staticmethod
    def get_file_extension(url):
        ext = url.split(".")[-1].split("?")[0].lower()
        return ext if ext in ("png", "jpg", "jpeg", "gif", "webp") else "png"

    @staticmethod
    async def cooldown_ok(interaction):
        """False, если человек обращается слишком часто (таймер в config.py).

        Ответ видит только он сам (ephemeral), поэтому спама в канале не будет.
        """
        allowed, remaining, _ = cooldown.check(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                dicts.get("cooldown_notice").format(seconds=math.ceil(remaining)),
                ephemeral=True,
            )
        return allowed

    async def ask(self, interaction, request):
        if not await self.cooldown_ok(interaction):
            return
        user_name = interaction.user.display_name
        await interaction.response.defer()
        response = await api.ask_ai(interaction.user.id, user_name, request)
        await interaction.followup.send(
            response.replace(user_name, f"<@{interaction.user.id}>")
        )

    @staticmethod
    async def forget(interaction):
        user_id = interaction.user.id
        if api.clear_history(user_id):
            await interaction.response.send_message(dicts.get("forgot_history"))
        else:
            await interaction.response.send_message(dicts.get("forget_history_clear"))

    async def art(self, interaction, query=None):
        if not await self.cooldown_ok(interaction):
            return
        await art_search.art_interaction(interaction, query)

    async def generate(self, interaction, prompt=None):
        if not await self.cooldown_ok(interaction):
            return
        await image_generation.generate_interaction(interaction, prompt)

    async def fur(self, interaction, query=None):
        if not query:
            await interaction.response.send_message(
                "\\*наклоняет голову\\* Ой, а что искать-то? 🦊\n"
                "Пиши теги на **английском** через пробел.\n"
                "Пример: `cute wolf solo`"
            )
            return

        if not await self.cooldown_ok(interaction):
            return

        await interaction.response.defer()

        tags_list = [
            t.strip().lower() for t in query.replace(" ", ",").split(",") if t.strip()
        ]
        if "safe" not in tags_list:
            tags_list.insert(0, "safe")
        tags_clean = ",".join(tags_list)

        try:
            encoded = urllib.parse.quote(tags_clean)
            url = f"https://furbooru.org/api/v1/json/search/images?q={encoded}&per_page=50"
            headers = {"User-Agent": "ExpieDiscordBot/1.0 (by Discord user)"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        log.error(
                            f"При поиске изображения сайт ответил кодом {resp.status}"
                        )
                        await interaction.followup.send(
                            f"{dicts.get('error_response')} {resp.status}\n"
                            "Может стоит попробовать позже, или проверить теги?"
                        )
                        return

                    data = await resp.json()
                    images = data.get("images", [])

                    if not images:
                        await interaction.followup.send(
                            f"\\*нюхает воздух\\* Ничего не нашёл по тегам `{query}`... "
                            "Может стоит попробовать другие слова, или проверить их правильность?"
                        )
                        return

                    image = random.choice(images)
                    img_url = image.get("representations", {}).get("full") or image.get(
                        "source_url"
                    )

                    if not img_url:
                        await interaction.followup.send(
                            "\\*наклоняет голову\\* Странно, я нашёл пост, но ссылка пустая..."
                        )
                        return

                    image_data, error = await self.fetch_image(session, img_url)
                    if error:
                        log.warning(f"Ошибка загрузки картинки: {error}")
                        await interaction.followup.send(
                            f"{dicts.get('error_response')} {error}"
                        )
                        return

                    ext = self.get_file_extension(img_url)
                    file = discord.File(
                        fp=io.BytesIO(image_data), filename=f"furbooru_art.{ext}"
                    )
                    await interaction.followup.send(
                        content=dicts.get_random("pic_comments"), file=file
                    )

        except aiohttp.ClientError as e:
            log.exception("Ошибка aiohttp.ClientError при генерации картинки")
            await interaction.followup.send(
                f"{dicts.get('error_response')} `{str(e)[:200]}`\nМожет попробуешь позже?"
            )
        except Exception as e:
            log.exception("Ошибка aiohttp при генерации картинки")
            await interaction.followup.send(
                f"{dicts.get('error_response')} `{str(e)[:200]}`\nМожет попробуешь позже?"
            )

    @staticmethod
    async def pair(interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "\\*прижимает уши\\* Это работает только на сервере!"
            )
            return

        online_users = [
            m
            for m in interaction.guild.members
            if not m.bot
            and m.status
            in (discord.Status.online, discord.Status.idle, discord.Status.dnd)
        ]

        if len(online_users) < 2:
            await interaction.response.send_message(
                "\\*нюхает воздух\\* Сейчас здесь слишком пусто для шипа..."
            )
            return

        u1, u2 = random.sample(online_users, 2)
        response = dicts.get_random(
            "couple_phrases", name1=u1.display_name, name2=u2.display_name
        )
        await interaction.response.send_message(response)

    @staticmethod
    async def hug(interaction, member=None):
        # Команда вызвана в ЛС
        if not interaction.guild:
            if member is None:
                await interaction.response.send_message(dicts.get_random("hug_pleased"))
            else:
                if member == interaction.client.user:
                    await interaction.response.send_message(
                        dicts.get_random("hug_pleased")
                    )
                else:
                    response = dicts.get_random("hug_phrases", mention=member.mention)
                    await interaction.response.send_message(response)
            return

        # Команда вызвана на сервере
        if member is None:
            users = [
                m
                for m in interaction.guild.members
                if not m.bot
                and m != interaction.user
                and m.status
                in (discord.Status.online, discord.Status.idle, discord.Status.dnd)
            ]
            if not users:
                await interaction.response.send_message(
                    "\\*нюхает\\* Я не чувствую никого поблизости!"
                )
                return
            member = random.choice(users)
        else:
            server_member = interaction.guild.get_member(member.id)
            if server_member:
                member = server_member

        # Если обнимаем бота
        if member == interaction.guild.me:
            await interaction.response.send_message(dicts.get_random("hug_pleased"))
            return

        # Обнимаем выбранного пользователя
        response = dicts.get_random("hug_phrases", mention=member.mention)
        await interaction.response.send_message(response)

    @staticmethod
    def _get_message_word(count: int) -> str:
        """Возвращает правильное окончание для слова 'сообщение' в зависимости от числа."""
        count = abs(count) % 100
        n1 = count % 10

        # 11-14 всегда "сообщений"
        if 10 < count < 20:
            return "сообщений"
        # 2, 3, 4 - "сообщения"
        if 1 < n1 < 5:
            return "сообщения"
        # 1 - "сообщение"
        if n1 == 1:
            return "сообщение"
        # 0, 5-9 - "сообщений"
        return "сообщений"

    @staticmethod
    async def stats(interaction: discord.Interaction):
        # ПРОВЕРКА: Команда только для серверов
        if interaction.guild is None:
            await interaction.response.send_message(
                "⚠️ Эта команда работает только на серверах!"
            )
            return

        # Сначала показываем "бот печатает..." (это даёт нам больше времени)
        await interaction.response.defer()

        # Получаем топ-10 из базы
        top_users = api.get_top_users(10)

        if not top_users:
            await interaction.followup.send(
                "Статистика пуста. Пока никто не писал боту!"
            )
            return

        message = "**🏆 Топ-10 самых активных пользователей:**\n\n"

        for i, user_data in enumerate(top_users, 1):
            user_id = user_data["user_id"]
            count = user_data["count"]

            # Статистика общая для всех серверов, поэтому человека может не быть
            # на этом сервере: тогда вместо имени пишем «Пользователь скрыт».
            username = utils.member_display_name(interaction.guild, user_id)

            # Медали
            match i:
                case 1:
                    medal = "🥇"
                case 2:
                    medal = "🥈"
                case 3:
                    medal = "🥉"
                case _:
                    medal = f"{i}."

            # Правильное окончание
            word = Defs._get_message_word(count)
            message += f"{medal} **{username}**: {count} {word}\n"

        # Используем followup вместо response.send_message
        await interaction.followup.send(message)


defs = Defs()
