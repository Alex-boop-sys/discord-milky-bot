"""Настройка стандартного модуля logging для Milky."""

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from config import paths

# ANSI-коды цветов для сообщений разных уровней.
COLORS = {
    logging.DEBUG: "\033[90m",  # Серый
    logging.INFO: "\033[92m",  # Ярко-зелёный
    logging.WARNING: "\033[93m",  # Жёлтый
    logging.ERROR: "\033[91m",  # Ярко-красный
    logging.CRITICAL: "\033[1;91m",  # Жирный ярко-красный
}

RESET_COLOR = "\033[0m"


# «Где это происходит» (сервер и канал) для текущей задачи. Обработчики событий
# выставляют его в начале, и все строки лога в этой задаче получают пометку сами.
_place = ContextVar("log_place", default="")


def place_name(guild, channel=None):
    """Название места для лога: «Сервер #канал», либо «ЛС» вне серверов."""
    if guild is None:
        return "ЛС"
    channel_name = getattr(channel, "name", None)
    return f"{guild.name} #{channel_name}" if channel_name else guild.name


def set_place(text):
    """Запоминает место для всех следующих строк лога в текущей задаче."""
    _place.set(text or "")


@contextmanager
def log_place(text):
    """Временно задаёт место для логов внутри блока with."""
    token = _place.set(text or "")
    try:
        yield
    finally:
        _place.reset(token)


class PlaceFilter(logging.Filter):
    """Подставляет в запись лога поле place (пусто, если место не задано)."""

    def filter(self, record):
        place = _place.get()
        record.place = f"[{place}] " if place else ""
        return True


class ColoredFormatter(logging.Formatter):
    """Добавляет цвет к сообщению в зависимости от уровня логирования."""

    def format(self, record):
        color = COLORS.get(record.levelno, "")
        message = super().format(record)

        if color:
            return f"{color}{message}{RESET_COLOR}"

        return message


# INFO — основной уровень для постоянно работающего бота.
# Файл ограничен по размеру, чтобы логи не заполнили накопитель сервера.
os.makedirs(paths.logs, exist_ok=True)

log = logging.getLogger("milky")
log.setLevel(logging.INFO)
log.propagate = False

# Не добавляем повторные обработчики при повторном импорте модуля.
if not log.handlers:
    formatter = ColoredFormatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s] (%(filename)s:%(lineno)d) %(place)s%(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        os.path.join(paths.logs, "milky.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(PlaceFilter())
    log.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(PlaceFilter())
    log.addHandler(console_handler)
