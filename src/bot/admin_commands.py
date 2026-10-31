"""Простые административные команды Милки для владельцев бота."""

from io import BytesIO
from pathlib import Path

import discord

from config import VERSION, env, paths
from src.utils.logger import log

ADMIN_TRIGGER = "root#"
HELP_TEXT = (
    "**Административные команды Милки**\n"
    "`root# ver` — показать версию программы\n"
    "`root# log` — отправить последние 10 строк лога в личные сообщения\n"
    "`root# help` — показать эту справку"
)


class AdminCommands:
    async def handle(self, message, request: str) -> bool:
        """Обрабатывает команду, если обращение к боту уже подтверждено.

        Возвращает True, если сообщение начинается с административного триггера.
        Вызывающий код запускает этот метод только для reply/упоминания/имени бота.
        """
        cleaned = request.strip()
        if not cleaned.lower().startswith(ADMIN_TRIGGER):
            return False

        command_text = cleaned[len(ADMIN_TRIGGER) :].strip().lower()
        # Убираем случайные пробелы и принимаем только первое слово как команду.
        command = command_text.split(maxsplit=1)[0] if command_text else "help"

        if message.author.id not in env.owner_ids:
            await message.reply(
                "⛔ Эта административная команда тебе недоступна.",
                mention_author=False,
            )
            log.warning(
                "Неавторизованная попытка выполнить root# от пользователя %s",
                message.author.id,
            )
            return True

        if command == "ver":
            await message.reply(
                f"Милки работает на версии `{VERSION}`.", mention_author=False
            )
        elif command == "log":
            await self._send_log(message)
        else:
            # help и любые неизвестные команды возвращают справку.
            await message.reply(HELP_TEXT, mention_author=False)
        return True

    @staticmethod
    async def _send_log(message):
        log_path = Path(paths.logs) / "milky.log"
        try:
            if log_path.is_file():
                with log_path.open("r", encoding="utf-8", errors="replace") as file:
                    last_lines = file.readlines()[-10:]
            else:
                last_lines = []

            content = "".join(last_lines).strip() or "Лог пока пуст."
            attachment = discord.File(
                fp=BytesIO(content.encode("utf-8")),
                filename="milky-last-10-lines.txt",
            )
            await message.author.send("Последние 10 строк лога Милки:", file=attachment)
            await message.reply(
                "📩 Отправила последние строки лога в личные сообщения.",
                mention_author=False,
            )
        except discord.Forbidden:
            await message.reply(
                "Не получилось отправить лог в личные сообщения. Разреши личные сообщения от участников сервера и повтори команду.",
                mention_author=False,
            )
        except (OSError, discord.HTTPException) as exc:
            log.exception("Не удалось отправить лог владельцу: %s", exc)
            await message.reply(
                "Не удалось прочитать или отправить лог.", mention_author=False
            )


admin_commands = AdminCommands()
