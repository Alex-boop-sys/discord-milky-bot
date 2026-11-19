import io
import random
import urllib.parse

import aiohttp
import discord

from src.utils.image_download import download_image, get_file_extension
from src.utils.json_parser import dicts
from src.utils.logger import log
from src.utils.utils import utils

DEFAULT_ART_QUERIES = [
    "expie_(gunsawian)",
    "casualties:_unknown",
    "gunsawian",
    "milky_(gunsawian)",
    "dune_(gunsawian)",
]


def force_safe_rating(parts):
    """Для обычных (не NSFW) каналов: вырезает любые rating:* из тегов пользователя
    и ставит rating:safe. Рейтинг определяет бот, а не пользователь."""
    return [p for p in parts if "rating:" not in p.lower()] + ["rating:safe"]


def _normalize_posts(data):
    """Поддерживает старый и актуальный форматы ответа e621."""
    if isinstance(data, list):
        posts = data
    elif isinstance(data, dict):
        posts = data["posts"] if isinstance(data.get("posts"), list) else []
    else:
        return []

    normalized = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        file_data = post.get("file")
        if isinstance(file_data, dict):
            normalized.append(post)
            continue

        # Новый формат API: files.original + files.meta.
        files = post.get("files")
        if not isinstance(files, dict):
            continue
        original = files.get("original")
        meta = files.get("meta")
        if not isinstance(original, dict):
            continue
        if not isinstance(meta, dict):
            meta = {}

        normalized_post = dict(post)
        normalized_post["file"] = {
            "url": original.get("url"),
            "size": meta.get("size", original.get("size", 0)),
            "ext": meta.get("ext"),
        }
        normalized.append(normalized_post)

    return normalized


class ArtSearch:
    @staticmethod
    async def art(message, query=None):
        """Обработчик для сообщений (без interaction)"""
        queries = DEFAULT_ART_QUERIES

        is_nsfw = query and utils.have_nsfw(query)

        if is_nsfw:
            if not utils.is_nsfw_channel(message.channel):
                await message.reply(dicts.get("nsfw_rejection"))
                return
            query = query.lower().replace("nsfw", "").strip()

        # Формируем теги. Рейтинг определяет канал: в обычном канале всегда safe.
        nsfw_channel = utils.is_nsfw_channel(message.channel)
        if not query:
            # Если запрос пустой - берём случайный из базовых
            tags_raw = random.choice(queries)
            # В NSFW-канале ищем explicit и questionable, в обычном - только safe
            tags_raw += " -rating:safe" if nsfw_channel else " rating:safe"
        else:
            parts = [
                p.strip().replace(" ", "_")
                for p in query.replace(",", " ").split()
                if p.strip()
            ]
            if nsfw_channel:
                # В NSFW-канале пользователь может указать рейтинг сам, иначе - не safe
                if not any("rating:" in p.lower() for p in parts):
                    parts.append("-rating:safe")
            else:
                parts = force_safe_rating(parts)
            tags_raw = " ".join(parts)

        tags = urllib.parse.quote(tags_raw, safe="")
        size_limit = utils.get_size_limit(message.guild)

        async with aiohttp.ClientSession() as session:
            try:
                url = f"https://e621.net/posts.json?tags={tags}&limit=50"
                headers = {"User-Agent": "ExpieDiscordBot/1.0 (by Discord user)"}

                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if not resp.ok:
                        log.error(f"e621 ответил {resp.status}")
                        await message.reply(
                            dicts.get("art_site_error").format(status=resp.status)
                        )
                        return

                    data = await resp.json()
                    posts = _normalize_posts(data)

                    if not posts:
                        log.warning(f"Ничего не найдено по тегам: {tags_raw}")
                        await message.reply(dicts.get("art_tags_not_found"))
                        return

                    # Фильтрация
                    valid_posts = []
                    fallback_posts = []
                    seen_urls = set()

                    for p in posts:
                        file_data = p.get("file")
                        if not file_data:
                            continue

                        img_url = file_data.get("url")
                        if not img_url or img_url in seen_urls:
                            continue
                        seen_urls.add(img_url)

                        ext = img_url.split(".")[-1].split("?")[0].lower()
                        if ext in ("webm", "swf", "mp4"):
                            continue

                        fallback_posts.append(p)

                        file_size = file_data.get("size", 0)
                        if file_size > size_limit:
                            continue

                        valid_posts.append(p)

                    if not valid_posts:
                        if fallback_posts:
                            post = random.choice(fallback_posts)
                            image_url = post["file"]["url"]
                            await message.reply(
                                dicts.get("art_large_image_link").format(
                                    image_url=image_url
                                )
                            )
                        else:
                            await message.reply(dicts.get("art_unavailable"))
                        return

                    posts_to_try = valid_posts.copy()
                    random.shuffle(posts_to_try)
                    max_attempts = min(5, len(posts_to_try))

                    for i, post in enumerate(posts_to_try[:max_attempts], 1):
                        image_url = post["file"]["url"]

                        try:
                            image_data, error = await download_image(
                                session, image_url, size_limit
                            )
                            if error:
                                continue

                            ext = get_file_extension(image_url)

                            file = discord.File(
                                fp=io.BytesIO(image_data), filename=f"expie_art.{ext}"
                            )
                            await message.reply(
                                content=dicts.get("img_found"),
                                file=file,
                            )
                            return

                        except discord.HTTPException as e:
                            if e.status == 413:
                                continue
                            raise

                    post = random.choice(valid_posts)
                    image_url = post["file"]["url"]
                    await message.reply(
                        dicts.get("art_download_fallback").format(
                            img_found=dicts.get("img_found"), image_url=image_url
                        )
                    )

            except Exception as e:
                log.exception("Ошибка при поиске изображения")
                await message.reply(
                    dicts.get("error_response_details").format(details=str(e)[:200])
                )

    @staticmethod
    async def fetch_image(session, url, timeout=20, max_bytes=8 * 1024 * 1024):
        # Оставлено как совместимая обёртка для существующих вызовов.
        return await download_image(session, url, max_bytes, timeout)

    @staticmethod
    def get_file_extension(url):
        return get_file_extension(url)

    async def art_interaction(self, interaction, query=None):
        # Базовые теги
        queries = DEFAULT_ART_QUERIES

        is_nsfw = query and utils.have_nsfw(query)

        # Проверка канала
        if is_nsfw:
            if not utils.is_nsfw_channel(interaction.channel):
                await interaction.response.send_message(
                    dicts.get("nsfw_rejection"),
                    ephemeral=True,
                )
                return
            query = query.lower().replace("nsfw", "").strip()

        await interaction.response.defer()

        # Теги. Рейтинг определяет бот: safe по умолчанию, explicit - только если
        # в запросе NSFW-слова (канал для этого уже проверен выше).
        if not query:
            tags_raw = random.choice(queries) + " rating:safe"
        else:
            parts = [
                p.strip().replace(" ", "_")
                for p in query.replace(",", " ").split()
                if p.strip()
            ]
            if is_nsfw:
                parts = [p for p in parts if "rating:" not in p.lower()]
                parts.append("rating:explicit")
            else:
                parts = force_safe_rating(parts)
            tags_raw = " ".join(parts)

        tags = urllib.parse.quote(tags_raw, safe="")
        size_limit = utils.get_size_limit(interaction.guild)

        try:
            url = f"https://e621.net/posts.json?tags={tags}&limit=75"
            headers = {"User-Agent": "ExpieDiscordBot/1.0 (by Discord user)"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if not resp.ok:
                        log.error(
                            f"При поиске изображения сайт ответил кодом {resp.status}"
                        )
                        await interaction.followup.send(
                            f"{dicts.get('error_response')} {resp.status}"
                        )
                        return

                    data = await resp.json()
                    posts = _normalize_posts(data)

                    if not posts:
                        await interaction.followup.send(dicts.get("fur_empty_results"))
                        return

                    # Фильтрация постов
                    valid_posts = []
                    fallback_posts = []
                    seen_urls = set()

                    for p in posts:
                        file_data = p.get("file")
                        if not file_data:
                            continue

                        img_url = file_data.get("url")
                        if not img_url or img_url in seen_urls:
                            continue
                        seen_urls.add(img_url)

                        # Пропуск видео/флеш
                        ext = img_url.split(".")[-1].split("?")[0].lower()
                        if ext in ("webm", "swf", "mp4"):
                            continue

                        fallback_posts.append(p)

                        # Проверка размера
                        file_size = file_data.get("size", 0)
                        if file_size > size_limit:
                            continue

                        valid_posts.append(p)

                    # Если нет подходящих по размеру
                    if not valid_posts:
                        if fallback_posts:
                            post = random.choice(fallback_posts)
                            image_url = post["file"]["url"]
                            await interaction.followup.send(
                                dicts.get("art_large_image_link").format(
                                    image_url=image_url
                                )
                            )
                        else:
                            await interaction.followup.send(
                                dicts.get("art_unavailable")
                            )
                        return

                    # Пробуем отправить файл
                    posts_to_try = valid_posts.copy()
                    random.shuffle(posts_to_try)
                    max_attempts = min(5, len(posts_to_try))

                    for i, post in enumerate(posts_to_try[:max_attempts], 1):
                        image_url = post["file"]["url"]

                        try:
                            image_data, error = await self.fetch_image(
                                session, image_url, max_bytes=size_limit
                            )
                            if error:
                                log.warning(f"Не удалось скачать {image_url}: {error}")
                                continue

                            if len(image_data) > size_limit:
                                continue

                            ext = self.get_file_extension(image_url)
                            file = discord.File(
                                fp=io.BytesIO(image_data), filename=f"expie_art.{ext}"
                            )
                            await interaction.followup.send(
                                content=dicts.get("img_found"),
                                file=file,
                            )
                            return

                        except discord.HTTPException as e:
                            if e.status == 413:
                                log.warning(
                                    f"Попытка {i}/{max_attempts}: 413 для {image_url}"
                                )
                                continue
                            raise

                    post = random.choice(valid_posts)
                    image_url = post["file"]["url"]
                    await interaction.followup.send(
                        dicts.get("art_download_fallback").format(
                            img_found=dicts.get("img_found"), image_url=image_url
                        )
                    )

        except Exception as e:
            log.exception("Ошибка при поиске изображения")
            await interaction.followup.send(
                dicts.get("error_response_details").format(details=str(e)[:200])
            )


art_search = ArtSearch()
