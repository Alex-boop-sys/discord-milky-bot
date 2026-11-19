"""Проверки общей загрузки изображений и ограничения размера."""

from types import SimpleNamespace

import pytest

from src.utils.image_download import download_image, get_file_extension


class FakeContent:
    def __init__(self, chunks):
        self.chunks = chunks

    async def iter_chunked(self, _chunk_size):
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    def __init__(self, status=200, chunks=(), headers=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.request = None

    def get(self, url, timeout):
        self.request = (url, timeout)
        return self.response


@pytest.mark.asyncio
async def test_download_image_reads_chunks_within_limit():
    session = FakeSession(FakeResponse(chunks=[b"abc", b"def"]))

    data, error = await download_image(session, "https://example.test/image.png", 6)

    assert data == b"abcdef"
    assert error is None


@pytest.mark.asyncio
async def test_download_image_stops_when_stream_exceeds_limit():
    session = FakeSession(FakeResponse(chunks=[b"abc", b"def"]))

    data, error = await download_image(session, "https://example.test/image.png", 5)

    assert data is None
    assert error == "too_large"


@pytest.mark.asyncio
async def test_download_image_rejects_large_content_length_without_reading():
    response = FakeResponse(chunks=[b"should not be read"], headers={"Content-Length": "100"})
    session = FakeSession(response)

    data, error = await download_image(session, "https://example.test/image.png", 10)

    assert data is None
    assert error == "too_large"


@pytest.mark.asyncio
async def test_download_image_returns_http_status():
    session = FakeSession(FakeResponse(status=404))

    data, error = await download_image(session, "https://example.test/missing.png", 10)

    assert data is None
    assert error == 404


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://example.test/art.PNG?size=large", "png"),
        ("https://example.test/path/image.webp", "webp"),
        ("https://example.test/image.unknown", "png"),
        ("https://example.test/image", "png"),
    ],
)
def test_get_file_extension(url, expected):
    assert get_file_extension(url) == expected
