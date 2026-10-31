"""NSFW-фильтры и выбор рейтинга для артов."""
import asyncio

import pytest

from src.features.art_search import art_search, force_safe_rating
from src.features.image_generation import image_generation
from src.utils.json_parser import dicts
from src.utils.utils import utils
from tests.helpers import make_message


@pytest.mark.parametrize(
    "text",
    ["nsfw", "+18 арт", "18+ арт", "rating:explicit", "rating:questionable", "fellatio", "pussy wolf", "hentai"],
)
def test_nsfw_words_are_detected(text):
    assert utils.have_nsfw(text)


@pytest.mark.parametrize("text", ["волк в лесу", "wolf", "analysis of cats", "cute fox", ""])
def test_ordinary_text_is_not_nsfw(text):
    assert not utils.have_nsfw(text)


def test_safe_rating_cannot_be_overridden_by_user():
    # Пользователь пытается обойти SFW-фильтр своим rating-тегом.
    for evil in ("rating:explicit", "rating:questionable", "-rating:safe", "~rating:e", "Rating:Explicit"):
        tags = force_safe_rating(["wolf", evil])
        assert tags == ["wolf", "rating:safe"], f"тег {evil} не вырезан: {tags}"


class _DM:
    """Личные сообщения: у такого канала нет метода is_nsfw()."""


class _Channel:
    def __init__(self, nsfw):
        self._nsfw = nsfw

    def is_nsfw(self):
        return self._nsfw


def test_is_nsfw_channel():
    assert utils.is_nsfw_channel(_Channel(True)) is True
    assert utils.is_nsfw_channel(_Channel(False)) is False
    assert utils.is_nsfw_channel(_DM()) is False   # ЛС считаем обычным каналом
    assert utils.is_nsfw_channel(None) is False    # канал неизвестен


def test_art_search_refuses_nsfw_in_ordinary_channel():
    message = make_message("арт", nsfw=False)
    asyncio.run(art_search.art(message, "nsfw лиса"))
    message.reply.assert_awaited_once_with(dicts.get("nsfw_rejection"))   # и до сети дело не дошло


def test_image_generation_refuses_nsfw_in_ordinary_channel():
    message = make_message("ген", nsfw=False)
    asyncio.run(image_generation.generate(message, "nsfw wolf"))
    message.reply.assert_awaited_once_with(dicts.get("nsfw_rejection"))
