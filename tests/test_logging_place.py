"""Название сервера и канала в строках лога."""
import asyncio
import logging
from types import SimpleNamespace

from src.utils.logger import PlaceFilter, log_place, place_name, set_place


def make_record():
    return logging.LogRecord("milky", logging.INFO, __file__, 1, "текст", None, None)


def test_place_name():
    guild = SimpleNamespace(name="Мой сервер")
    assert place_name(None) == "ЛС"
    assert place_name(guild, SimpleNamespace(name="общий")) == "Мой сервер #общий"
    assert place_name(guild) == "Мой сервер"


def test_filter_adds_place_inside_context_only():
    record = make_record()
    with log_place("Мой сервер #общий"):
        PlaceFilter().filter(record)
        assert record.place == "[Мой сервер #общий] "
    after = make_record()
    PlaceFilter().filter(after)
    assert after.place == ""                               # за пределами блока места нет


def test_places_of_parallel_events_do_not_mix():
    """Каждое событие Discord идёт в своей задаче и видит только своё место."""

    async def event(name):
        set_place(name)
        await asyncio.sleep(0.01)                          # здесь соседние события успевают отработать
        record = make_record()
        PlaceFilter().filter(record)
        return record.place

    async def main():
        return await asyncio.gather(event("A #1"), event("B #2"), event("C #3"))

    assert asyncio.run(main()) == ["[A #1] ", "[B #2] ", "[C #3] "]
