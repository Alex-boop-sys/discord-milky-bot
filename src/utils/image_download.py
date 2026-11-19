"""Безопасная загрузка изображений с ограничением расхода памяти."""

from urllib.parse import urlsplit


CHUNK_SIZE = 64 * 1024
DEFAULT_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webp")


async def download_image(session, url, max_bytes, timeout=20):
    """Скачивает изображение, не позволяя ответу превысить max_bytes.

    Возвращает (байты, None) при успехе или (None, код_статуса/'too_large').
    Данные читаются частями: Content-Length используется как ранняя проверка,
    но фактический размер всё равно контролируется во время загрузки.
    """
    import aiohttp

    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as response:
        if response.status != 200:
            return None, response.status

        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    return None, "too_large"
            except ValueError:
                pass

        image_data = bytearray()
        async for chunk in response.content.iter_chunked(CHUNK_SIZE):
            if len(image_data) + len(chunk) > max_bytes:
                return None, "too_large"
            image_data.extend(chunk)

        return bytes(image_data), None


def get_file_extension(url):
    """Возвращает поддерживаемое расширение из URL, иначе png."""
    extension = urlsplit(url).path.rsplit(".", 1)
    if len(extension) == 2 and extension[1].lower() in DEFAULT_IMAGE_EXTENSIONS:
        return extension[1].lower()
    return "png"
