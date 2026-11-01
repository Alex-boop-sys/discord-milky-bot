"""Список пользователей, которых Милки игнорирует (баны).

Хранится в data/ignored_users.json (в git не попадает), при запуске читается один раз,
дальше все проверки идут по словарю в памяти: это мгновенно. Изменения (ban/unban из
админ-команд) сразу применяются и атомарно записываются на диск, перезапуск не нужен.

Формат файла (совместим с прежним):
    {"ignored_users": {"<id>": {"nickname": "...", "banned_at": "2026-...Z",
                                "expires_at": "2026-...Z", "reason": "...",
                                "banned_by": <id владельца>}}}
Записи без expires_at бессрочные.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from config import paths
from src.utils.logger import log

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_time(value):
    """Строка ISO (в том числе с «Z» на конце) -> aware datetime; None, если не получилось."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # без пояса = UTC


class IgnoreList:
    def __init__(self, path=None):
        self.path = path or paths.ignored_users
        self._entries = {}
        self._load()

    # ---------------------------------------------------------------- проверки

    def is_ignored(self, user_id):
        """Игнорируется ли пользователь прямо сейчас (быстрая проверка по памяти)."""
        entry = self._entries.get(str(user_id))
        return entry is not None and not self._expired(entry)

    def get(self, user_id):
        """Запись об игнорируемом пользователе или None, если не игнорируется."""
        entry = self._entries.get(str(user_id))
        return entry if entry is not None and not self._expired(entry) else None

    def active(self):
        """Действующие записи: список (user_id, запись), старые баны сначала."""
        items = [(int(uid), entry) for uid, entry in self._entries.items() if not self._expired(entry)]
        return sorted(items, key=lambda item: item[1].get("banned_at", ""))

    # ---------------------------------------------------------------- изменения

    def add(self, user_id, nickname, hours=None, reason="", banned_by=None, now=None):
        """Добавляет (или обновляет) запись. hours=None - бессрочно. Возвращает запись."""
        now = now or datetime.now(timezone.utc)
        entry = {
            "nickname": nickname,
            "banned_at": now.strftime(TIME_FORMAT),
            "reason": reason,
        }
        if hours is not None:
            entry["expires_at"] = (now + timedelta(hours=hours)).strftime(TIME_FORMAT)
        if banned_by is not None:
            entry["banned_by"] = banned_by

        updated = dict(self._entries)
        updated[str(user_id)] = entry
        self._commit(updated)
        return entry

    def remove(self, user_id):
        """Убирает пользователя из списка. Возвращает его запись или None, если его там не было."""
        entry = self.get(user_id)
        updated = {uid: e for uid, e in self._entries.items() if uid != str(user_id)}
        if entry is not None or len(updated) != len(self._entries):
            self._commit(updated)
        return entry

    # ------------------------------------------------------------ загрузка и запись

    def _expired(self, entry):
        expires = parse_time(entry.get("expires_at"))
        # Нет срока или дата записана неверно - считаем бан бессрочным:
        # лучше оставить игнор, чем случайно его снять.
        return expires is not None and datetime.now(timezone.utc) >= expires

    def _load(self):
        if not os.path.isfile(self.path):
            return  # списка ещё нет: никого не игнорируем, файл появится с первым баном
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
            entries = raw["ignored_users"]
            if not isinstance(entries, dict):
                raise ValueError("ignored_users должен быть словарём")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._back_up_broken_file(exc)
            return

        for uid, entry in entries.items():
            if not str(uid).isdigit() or not isinstance(entry, dict):
                log.warning(f"Игнор-лист: запись {uid!r} пропущена (неверный формат)")
                continue
            if entry.get("expires_at") and parse_time(entry["expires_at"]) is None:
                log.warning(f"Игнор-лист: у {uid} неверная дата expires_at, бан считаю бессрочным")
            self._entries[str(uid)] = entry

        # Истёкшие баны не храним: чистим память и файл сразу.
        expired = [uid for uid, entry in self._entries.items() if self._expired(entry)]
        if expired:
            self._commit({uid: e for uid, e in self._entries.items() if uid not in expired})
        log.info(f"Игнор-лист загружен: {len(self._entries)} записей")

    def _back_up_broken_file(self, error):
        """Повреждённый файл не затираем: переименовываем в резервную копию и начинаем с пустого."""
        backup = f"{self.path}.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(self.path, backup)
            where = f"сохранён как {os.path.basename(backup)}"
        except OSError:
            where = "не удалось сохранить копию"
        log.error(f"Игнор-лист: файл повреждён ({error}); {where}. Список пока пуст!")

    def _commit(self, entries):
        """Сначала записывает на диск, и только потом применяет в памяти."""
        self._write(entries)
        self._entries = entries

    def _write(self, entries):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Пишем во временный файл рядом и подменяем целиком: при сбое старый файл цел.
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                json.dump({"ignored_users": entries}, file, ensure_ascii=False, indent=4)
                file.write("\n")
            os.replace(tmp_path, self.path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise


ignore_list = IgnoreList()
