import io
import random
import urllib.parse

import aiohttp
import discord

from src.utils.json_parser import dicts
from src.utils.logger import log
from src.utils.utils import utils


class ImageGeneration:
    @staticmethod
    async def generate(message, prompt=None):
        """Обработчик генерации для сообщений (без interaction)"""
        is_nsfw = prompt and utils.have_nsfw(prompt)

        # Проверка наличия промпта
        if not prompt:
            prompt = (
                "solo, cute, fluffy, black melanistic fur, anthro, furry, "
                "wolf-fox hybrid, big eyes, orange sclera, big fluffy tail, "
                "orange tip tail, three ears, high quality, kawaii style, "
                "beautiful background"
            )

        # Проверка канала
        if is_nsfw:
            if not utils.is_nsfw_channel(message.channel):
                await message.reply(dicts.get("nsfw_rejection"))
                return

        # Показываем статус "печатает"
        async with message.channel.typing():
            enhanced_prompt = f"{prompt}, detailed, soft lighting, expression"
            encoded = urllib.parse.quote(enhanced_prompt)
            seed = random.randint(1, 999999)
            image_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        image_url, timeout=aiohttp.ClientTimeout(total=60)
                    ) as resp:
                        if resp.status != 200:
                            await message.reply(dicts.get("error_response"))
                            return

                        image_data = await resp.read()

                        file = discord.File(
                            fp=io.BytesIO(image_data), filename="expie_generated.png"
                        )
                        display_prompt = (
                            prompt if len(prompt) < 300 else f"{prompt[:300]}..."
                        )
                        await message.reply(
                            content=f"\\*виляет хвостом\\* Есть! По запросу: `{display_prompt}`",
                            file=file,
                        )
            except Exception as e:
                log.exception("Ошибка при генерации изображения")
                await message.reply(f"{dicts.get('error_response')} {str(e)[:200]}")

    @staticmethod
    async def fetch_image(session, url, timeout=20):
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None, resp.status
            return await resp.read(), None

    async def generate_interaction(self, interaction, prompt=None):
        is_nsfw = prompt and utils.have_nsfw(prompt)

        # Проверка наличия промпта
        if not prompt:
            prompt = (
                "solo, cute, fluffy, black melanistic fur, anthro, furry, "
                "wolf-fox hybrid, big eyes, orange sclera, big fluffy tail, "
                "orange tip tail, three ears, high quality, kawaii style, "
                "beautiful background"
            )

        # Проверка канала
        if is_nsfw:
            if not utils.is_nsfw_channel(interaction.channel):
                await interaction.response.send_message(
                    dicts.get("nsfw_rejection"),
                    ephemeral=True,
                )
                return

        await interaction.response.defer()
        enhanced_prompt = f"{prompt}, detailed, soft lighting, expression"
        encoded = urllib.parse.quote(enhanced_prompt)
        seed = random.randint(1, 999999)
        image_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}"

        try:
            async with aiohttp.ClientSession() as session:
                image_data, error = await self.fetch_image(
                    session, image_url, timeout=60
                )
                if error:
                    await interaction.followup.send(dicts.get("error_response"))
                    return

                file = discord.File(
                    fp=io.BytesIO(image_data), filename="expie_generated.png"
                )
                display_prompt = prompt if len(prompt) < 300 else f"{prompt[:300]}..."
                await interaction.followup.send(
                    content=f"\\*виляет хвостом\\* Есть! По запросу: `{display_prompt}`",
                    file=file,
                )
        except Exception as e:
            log.exception("Ошибка при генерации изображения")
            await interaction.followup.send(
                f"{dicts.get('error_response')} {str(e)[:200]}"
            )


image_generation = ImageGeneration()
