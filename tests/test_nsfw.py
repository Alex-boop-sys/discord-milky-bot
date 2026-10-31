"""NSFW-фильтры и выбор рейтинга для артов."""
import pytest

from src.features.art_search import force_safe_rating
from src.utils.utils import utils


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
