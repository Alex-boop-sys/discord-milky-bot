"""Список игнорируемых: хранение, сроки, безопасная запись, повреждённые файлы."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.services.ignore_list import IgnoreList, parse_time

# Дата в далёком будущем, чтобы баны в тестах не «истекали» по реальным часам.
NOW = datetime(2099, 11, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "data" / "ignored_users.json")


def read(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


# ---------------------------------------------------------------- пустой список

def test_missing_file_means_empty_list_and_is_not_created(path):
    lst = IgnoreList(path)
    assert lst.active() == []
    assert not lst.is_ignored(1) and lst.get(1) is None
    assert not os.path.exists(path)                     # файл появится только с первым баном


# ------------------------------------------------------------------- добавление

def test_permanent_ban_is_saved_and_survives_restart(path):
    lst = IgnoreList(path)
    entry = lst.add(123456789012345678, "Алекс", reason="спам", banned_by=5, now=NOW)

    assert entry == {"nickname": "Алекс", "banned_at": "2099-11-01T12:00:00Z", "reason": "спам", "banned_by": 5}
    assert lst.is_ignored(123456789012345678) and lst.is_ignored("123456789012345678")   # int и str одинаково
    assert read(path)["ignored_users"]["123456789012345678"] == entry

    assert IgnoreList(path).is_ignored(123456789012345678)                               # «после перезапуска»


def test_temporary_ban_has_expiry(path):
    lst = IgnoreList(path)
    entry = lst.add(1, "Алекс", hours=36, now=NOW)
    assert entry["expires_at"] == "2099-11-03T00:00:00Z"   # 12:00 + 36 часов
    assert lst.is_ignored(1)


def test_ban_expires_and_expired_entries_are_cleaned_on_load(path):
    past = datetime.now(timezone.utc) - timedelta(hours=5)
    lst = IgnoreList(path)
    lst.add(1, "Истёк", hours=1, now=past)
    lst.add(2, "Бессрочно")
    assert not lst.is_ignored(1) and lst.is_ignored(2)
    assert [uid for uid, _ in lst.active()] == [2]

    IgnoreList(path)                                    # загрузка чистит и файл
    assert list(read(path)["ignored_users"]) == ["2"]


def test_adding_again_renews_the_entry(path):
    lst = IgnoreList(path)
    lst.add(1, "Алекс", hours=1, reason="первый раз", now=NOW)
    lst.add(1, "Алекс", reason="второй раз", now=NOW)   # теперь бессрочно
    entry = lst.get(1)
    assert entry["reason"] == "второй раз" and "expires_at" not in entry
    assert len(lst.active()) == 1


def test_active_is_sorted_by_ban_time(path):
    lst = IgnoreList(path)
    lst.add(3, "Третий", now=NOW + timedelta(hours=2))
    lst.add(1, "Первый", now=NOW)
    lst.add(2, "Второй", now=NOW + timedelta(hours=1))
    assert [uid for uid, _ in lst.active()] == [1, 2, 3]


# --------------------------------------------------------------------- удаление

def test_remove_returns_entry_once_and_persists(path):
    lst = IgnoreList(path)
    lst.add(1, "Алекс", now=NOW)
    assert lst.remove(1)["nickname"] == "Алекс"
    assert lst.remove(1) is None
    assert not lst.is_ignored(1)
    assert not IgnoreList(path).is_ignored(1)


def test_removing_unknown_user_does_not_create_file(path):
    assert IgnoreList(path).remove(1) is None
    assert not os.path.exists(path)


# -------------------------------------------------- старый формат и кривые записи

def test_old_file_format_still_works(path):
    os.makedirs(os.path.dirname(path))
    old_format = {"ignored_users": {
        "111111111111111111": {"nickname": "Старый", "banned_at": "2026-01-01T00:00:00Z",
                               "expires_at": "2999-01-01T00:00:00Z", "reason": "тест"},
        "222222222222222222": {"nickname": "Бессрочный", "banned_at": "2026-01-01T00:00:00Z", "reason": ""},
    }}
    with open(path, "w", encoding="utf-8", newline="\r\n") as file:      # как у вас: переводы строк CRLF
        json.dump(old_format, file, ensure_ascii=False, indent=4)
    lst = IgnoreList(path)
    assert lst.is_ignored(111111111111111111) and lst.is_ignored(222222222222222222)


def test_bad_entries_are_skipped_but_good_ones_kept(path):
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"ignored_users": {"abc": {}, "5": "не словарь", "7": {"nickname": "Нормальный"}}}, file)
    lst = IgnoreList(path)
    assert [uid for uid, _ in lst.active()] == [7]


def test_invalid_expiry_date_keeps_ban_permanent(path):
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"ignored_users": {"7": {"nickname": "Кривая дата", "expires_at": "не дата"}}}, file)
    assert IgnoreList(path).is_ignored(7)               # лучше оставить игнор, чем случайно снять


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-11-01T12:00:00Z", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)),
        ("2026-11-01T12:00:00+00:00", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)),
        ("2026-11-01T15:00:00+03:00", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)),
        ("2026-11-01T12:00:00", datetime(2026, 11, 1, 12, tzinfo=timezone.utc)),   # без пояса = UTC
        ("мусор", None), ("", None), (None, None), (12345, None),
    ],
)
def test_parse_time(value, expected):
    parsed = parse_time(value)
    assert (parsed.astimezone(timezone.utc) if parsed else None) == expected


# --------------------------------------------------------------- повреждённые файлы

@pytest.mark.parametrize("content", ["{ это не json", "[]", '{"ignored_users": []}', '{"другое": {}}', ""])
def test_broken_file_is_backed_up_not_overwritten(path, content):
    os.makedirs(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)

    lst = IgnoreList(path)
    assert lst.active() == []                           # работаем с пустым списком
    backups = [name for name in os.listdir(os.path.dirname(path)) if ".broken-" in name]
    assert len(backups) == 1                            # а испорченный файл сохранён целиком
    with open(os.path.join(os.path.dirname(path), backups[0]), encoding="utf-8") as file:
        assert file.read() == content

    lst.add(1, "Алекс", now=NOW)                        # и новый бан спокойно записывается
    assert read(path)["ignored_users"]["1"]["nickname"] == "Алекс"


# ------------------------------------------------------------------ запись на диск

def test_failed_save_changes_nothing(path, monkeypatch):
    lst = IgnoreList(path)
    lst.add(1, "Алекс", now=NOW)

    def broken_replace(src, dst):
        raise OSError("диск переполнен")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        lst.add(2, "Не сохранится", now=NOW)
    with pytest.raises(OSError):
        lst.remove(1)

    assert lst.is_ignored(1) and not lst.is_ignored(2)  # память не разошлась с диском
    assert list(read(path)["ignored_users"]) == ["1"]
    assert not [n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")]   # мусора нет


def test_file_is_readable_utf8_with_newline_at_end(path):
    IgnoreList(path).add(1, "Привет, Мир", reason="кириллица", now=NOW)
    with open(path, "rb") as file:
        raw = file.read()
    assert "Привет, Мир".encode("utf-8") in raw         # не \u0420\u043f...
    assert raw.endswith(b"\n") and b"\r\n" not in raw
