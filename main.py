# main.py
import asyncio
import os

from src.bot.bot import bot_handlers
from src.bot.commands import register_slash_commands
from src.utils.logger import log
from src.utils.utils import utils


async def main():
    log.info(f"Начало работы основного цикла (PID: {os.getpid()})")
    bot_task = asyncio.create_task(bot_handlers.run_bot())
    try:
        await bot_task
    except asyncio.CancelledError:
        utils.exit("Завершение работы")
    except Exception as e:
        utils.exit(f"Ошибка запуска: {e}", 1)


if __name__ == "__main__":
    utils.check_tokens()
    bot_handlers.register_handlers()
    register_slash_commands()
    asyncio.run(main())
