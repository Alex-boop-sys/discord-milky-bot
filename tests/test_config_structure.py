"""Проверки структуры проекта (без подключения к Discord)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_expected_directories_exist():
    # data/ и logs/ создаются при запуске бота и в git не хранятся, поэтому их тут нет.
    for relative in ("resources", "src/bot/handlers", "src/features", "src/services", "src/utils"):
        assert (ROOT / relative).is_dir(), f"нет папки {relative}"


def test_required_files_exist():
    for relative in (
        "main.py",
        "config.py",
        "requirements.txt",
        ".env.example",
        "resources/dicts.json",
        "resources/ignored_users.example.json",
    ):
        assert (ROOT / relative).is_file(), f"нет файла {relative}"


def test_json_resources_are_valid():
    # Если в JSON потерялась запятая или кавычка, бот упадёт при запуске.
    for name in ("dicts.json", "ignored_users.example.json"):
        with open(ROOT / "resources" / name, encoding="utf-8") as f:
            json.load(f)
