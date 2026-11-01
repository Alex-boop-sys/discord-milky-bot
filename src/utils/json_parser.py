# src/utils/json_parser.py
import json
import os
import random

from config import paths
from src.utils.debugger import Debugger
from src.utils.logger import log
from src.utils.utils import utils


class JsonReader:
    def __init__(self, path, is_required: bool = True):
        self.path = path
        self.filename = os.path.basename(path)
        self.is_required = is_required

        self.data = {}
        self._load()

    # ЗАГРУЗКА
    def _load(self) -> None:
        if not os.path.exists(self.path):
            if self.is_required:
                utils.exit(f'Обязательный файл "{self.filename}" не найден', 1)
            else:
                log.warning(f'Необязательный файл "{self.filename}" не найден')
                return

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
                log.info(f"Загружено {len(self.data)} ключей из {self.filename}")
        except json.JSONDecodeError as e:
            utils.exit(f'Ошибка декодирования "{self.filename}": {e}', 1)
        except Exception as e:
            utils.exit(f'Ошибка при загрузке "{self.filename}": {e}', 1)

    # СЛУЧАЙНЫЙ ВАРИАНТ ПО КЛЮЧУ
    def get_random(self, key: str, **kwargs):
        items = self.data.get(key, [])
        if not items:
            log.warning(f"Значение '{key}' не найдено в \"{self.filename}\"")
            return None

        if not isinstance(items, list):
            log.error(f"Значение '{key}' в \"{self.filename}\" не является списком")
            return None

        choice = random.choice(items)

        if kwargs and isinstance(choice, str):
            try:
                return choice.format(**kwargs)
            except KeyError as e:
                log.error(
                    f"Ошибка форматирования для ключа '{key}': отсутствует ключ {e}"
                )
                return choice
            except Exception as e:
                log.error(f"Ошибка форматирования для ключа '{key}': {e}")
                return choice
        return choice

    # ПЕРЕЗАГРУЗКА
    def reload(self) -> None:
        log.info(f"Перезагрузка {self.filename}")
        self._load()

    # ВСЕ КЛЮЧИ
    def keys(self):
        return list(self.data.keys())

    # ПО КЛЮЧУ
    def get(self, key, default=None):
        return self.data.get(key, default)

    # СПИСОК ПАР
    def items(self) -> list:
        return list(self.data.items())

    # СЛУЖЕБНЫЕ
    def __getattr__(self, name: str):
        if name.startswith("_"):
            return super().__getattr__(name)
        if name in self.data:
            return self.data[name]
        log.error(f"Ключ '{name}' не найден в \"{self.filename}\"")
        return None

    def __getitem__(self, key: str):
        if key in self.data:
            return self.data[key]
        log.error(f"Ключ '{key}' не найден в \"{self.filename}\"")
        return None

    def __setitem__(self, key: str, value):
        self.data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"JsonReader('{self.filename}', keys={len(self.data)}, required={self.is_required})"

    def __str__(self) -> str:
        return f"JsonReader: {self.filename} ({len(self.data)} ключей)"


if __name__ == "__main__":
    debugger = Debugger(__file__)
    file = input("Имя файла: ")
    reader = JsonReader(os.path.join(paths.resources, file), is_required=False)
    debugger.execute_cycle()

dicts = JsonReader(paths.dicts, is_required=True)
