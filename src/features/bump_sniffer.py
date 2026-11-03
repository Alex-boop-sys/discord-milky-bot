"""Диагностика экономики: что Милки «видит» в сообщениях бамп-ботов.

Временный инструмент для поиска причин, почему бамп не засчитывается:
  - describe_message(): всё, что Милки видит в одном сообщении, и решение экономики;
  - BumpSniffer: на ограниченное время записывает сообщения ботов (создание и правки)
    на серверах с экономикой, чтобы потом посмотреть их одним файлом.
Управляется админ-командами root# sniff и root# inspect. Записи хранятся только в
памяти и пропадают при перезапуске; людей и самого Милки в них нет.
"""

import json
import time
from collections import Counter, deque
from datetime import datetime, timezone

from src.features.economy import _component_texts, collect_text, economy, legacy_interaction
from src.utils.logger import log

DEFAULT_MINUTES = 30
MAX_MINUTES = 240
MAX_RECORDS = 200
TEXT_LIMIT = 1500   # длинные тексты в записях обрезаются

VERDICT_TEXT = {
    "reward": "награда напрямую",
    "minigame": "сбор реакций 🫐",
    "wait": "ждёт правки сообщения",
    "ignore": "игнорирует",
}


def _cut(text, limit=TEXT_LIMIT):
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat() if moment else None


def describe_message(message, raw=None):
    """Всё, что Милки видит в сообщении, в виде словаря (для JSON-файла и сводки)."""
    author = getattr(message, "author", None)
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    metadata = getattr(message, "interaction_metadata", None)
    interaction = legacy_interaction(message)
    flags = getattr(message, "flags", None)
    reference = getattr(message, "reference", None)

    embeds = []
    for embed in getattr(message, "embeds", None) or []:
        embeds.append(
            {
                "title": _cut(getattr(embed, "title", None)),
                "description": _cut(getattr(embed, "description", None)),
                "author": getattr(getattr(embed, "author", None), "name", None),
                "footer": getattr(getattr(embed, "footer", None), "text", None),
                "fields": [
                    {"name": _cut(getattr(f, "name", None), 200), "value": _cut(getattr(f, "value", None), 500)}
                    for f in getattr(embed, "fields", None) or []
                ],
            }
        )

    report = {
        "message_id": getattr(message, "id", None),
        "guild": {"id": getattr(guild, "id", None), "name": getattr(guild, "name", None)},
        "channel": {"id": getattr(channel, "id", None), "name": getattr(channel, "name", None)},
        "author": {
            "id": getattr(author, "id", None),
            "name": getattr(author, "name", None),
            "bot": getattr(author, "bot", None),
        },
        "type": str(getattr(message, "type", None)),
        "flags": [name for name, value in flags if value] if flags is not None else [],
        "created_at": _iso(getattr(message, "created_at", None)),
        "edited_at": _iso(getattr(message, "edited_at", None)),
        "webhook_id": getattr(message, "webhook_id", None),
        "reply_to": getattr(reference, "message_id", None) if reference else None,
        "interaction_metadata": {
            "user_id": getattr(getattr(metadata, "user", None), "id", None),
            "type": str(getattr(metadata, "type", None)),
            "original_response_message_id": getattr(metadata, "original_response_message_id", None),
        }
        if metadata
        else None,
        "interaction": {
            "name": getattr(interaction, "name", None),
            "user_id": getattr(getattr(interaction, "user", None), "id", None),
        }
        if interaction
        else None,
        "content": _cut(getattr(message, "content", None)),
        "embeds": embeds,
        "components_text": [_cut(t, 500) for t in _component_texts(getattr(message, "components", None))],
        "text_seen_by_milky": _cut(collect_text(message)),
        "diagnosis": economy.diagnose(message),
    }
    if raw is not None:
        report["raw"] = raw
    return report


def format_diagnosis(diagnosis):
    """Решение экономики в виде строк для чата: галочки по проверкам и итог."""
    lines = []
    for check in diagnosis["checks"]:
        line = f"{'✅' if check['ok'] else '❌'} {check['name']}"
        if check["detail"]:
            line += f" — {check['detail']}"
        lines.append(line)
    lines.append(f"**Итог:** {diagnosis['summary']}")
    return lines


def to_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


class BumpSniffer:
    """Записывает сообщения ботов на ограниченное время. Только в памяти."""

    def __init__(self, max_records=MAX_RECORDS):
        self._records = deque(maxlen=max_records)
        self._until = 0.0

    # --------------------------------------------------------------- управление

    def is_active(self, now=None):
        return (time.time() if now is None else now) < self._until

    def remaining(self, now=None):
        """Сколько секунд осталось до автоматического выключения."""
        return max(0.0, self._until - (time.time() if now is None else now))

    def start(self, minutes=DEFAULT_MINUTES, now=None):
        now = time.time() if now is None else now
        self._until = now + minutes * 60
        log.info(f"Диагностика бампов включена на {minutes} мин")
        return self._until

    def stop(self):
        self._until = 0.0
        log.info("Диагностика бампов выключена")

    def clear(self):
        self._records.clear()

    def count(self):
        return len(self._records)

    # ------------------------------------------------------------------- запись

    def record(self, kind, message, raw=None, self_id=None, now=None):
        """Запоминает сообщение бота (kind: "create" или "edit"). Возвращает True, если записано."""
        if not self.is_active(now):
            return False
        author = getattr(message, "author", None)
        guild = getattr(message, "guild", None)
        if author is None or not getattr(author, "bot", False) or author.id == self_id:
            return False
        if guild is None or not economy.enabled_in(guild.id):
            return False

        try:
            entry = describe_message(message, raw=raw)
        except Exception as exc:  # диагностика никогда не должна ломать бота
            log.warning(f"Диагностика: не удалось описать сообщение {getattr(message, 'id', '?')}: {exc}")
            entry = {"message_id": getattr(message, "id", None), "error": str(exc)}
        entry = {"event": kind, "at": datetime.now(timezone.utc).isoformat(), **entry}
        self._records.append(entry)
        return True

    # ------------------------------------------------------------------ просмотр

    def snapshot(self):
        return list(self._records)

    def summary_lines(self):
        """Короткая сводка для чата: по каждому боту число записей и вердикт последней."""
        by_author = {}
        for entry in self._records:
            author = entry.get("author") or {}
            key = author.get("id")
            by_author.setdefault(key, []).append(entry)

        lines = []
        for author_id, entries in by_author.items():
            name = (entries[-1].get("author") or {}).get("name") or "?"
            kinds = Counter(e["event"] for e in entries)
            last = entries[-1].get("diagnosis") or {}
            verdict = VERDICT_TEXT.get(last.get("verdict"), "?")
            lines.append(
                f"• **{name}** (`{author_id}`): записей {len(entries)} "
                f"(новых {kinds.get('create', 0)}, правок {kinds.get('edit', 0)}); "
                f"последнее: {verdict}"
            )
        return lines


bump_sniffer = BumpSniffer()
