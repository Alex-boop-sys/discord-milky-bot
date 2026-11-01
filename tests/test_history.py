"""История диалогов и статистика (на временной базе, настоящая не трогается)."""
import sqlite3

import pytest

from config import paths, var
from src.services.history import HistoryService


@pytest.fixture
def hist(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "db_path", str(tmp_path / "history.db"))
    monkeypatch.setattr(var, "max_history", 4)
    return HistoryService()


def count_rows(hist, user_id):
    return hist.conn.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,)).fetchone()[0]


def test_build_messages_adds_system_prompt_and_name(hist):
    messages = hist.build_messages(1, "Алекс", "привет")
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "Алекс: привет"}


def test_context_is_limited_by_max_history(hist):
    for i in range(10):
        hist.build_messages(1, "Алекс", f"сообщение {i}")
        hist.save_response(1, f"ответ {i}")
    messages = hist.build_messages(1, "Алекс", "последнее")
    assert len(messages) - 1 == var.max_history          # без системного промпта
    assert messages[-1]["content"] == "Алекс: последнее"
    assert count_rows(hist, 1) <= var.max_history + 1    # старое реально удаляется


def test_other_users_do_not_mix(hist):
    hist.build_messages(1, "А", "от первого")
    hist.build_messages(2, "Б", "от второго")
    contents = [m["content"] for m in hist.build_messages(1, "А", "ещё")[1:]]
    assert all("от второго" not in c for c in contents)


def test_forget_deletes_history_but_keeps_stats(hist):
    for i in range(3):
        hist.build_messages(1, "А", f"м{i}")
        hist.save_response(1, "ответ")
    assert hist.clear_history(1) is True
    assert count_rows(hist, 1) == 0                       # физически удалено
    assert hist.get_top_users() == [{"user_id": 1, "count": 3}]   # счётчик остался
    assert hist.clear_history(1) is False                 # забывать уже нечего


def test_stats_are_sorted_and_not_limited_by_history(hist):
    for _ in range(9):
        hist.build_messages(1, "А", "x")
        hist.save_response(1, "y")
    hist.build_messages(2, "Б", "x")
    top = hist.get_top_users()
    assert top[0] == {"user_id": 1, "count": 9}           # хотя в истории осталось <= 5 строк
    assert top[1] == {"user_id": 2, "count": 1}


def test_save_response_escapes_asterisks_only_for_display(hist):
    hist.build_messages(1, "А", "привет")
    assert hist.save_response(1, "*фыркает*") == "\\*фыркает\\*"
    stored = hist.conn.execute("SELECT content FROM history WHERE role = 'assistant'").fetchone()[0]
    assert stored == "*фыркает*"


def test_old_database_format_is_refused(tmp_path, monkeypatch):
    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, user_id INTEGER, role TEXT, content TEXT, is_active INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "db_path", str(old_db))
    with pytest.raises(RuntimeError, match="старый формат"):
        HistoryService()


def test_summary_for_status_command(hist):
    assert hist.get_summary() == {"history_rows": 0, "users": 0, "total_messages": 0}
    hist.build_messages(1, "А", "привет")
    hist.save_response(1, "ответ")
    hist.build_messages(2, "Б", "привет")
    summary = hist.get_summary()
    assert summary == {"history_rows": 3, "users": 2, "total_messages": 2}
    hist.clear_history(1)                                   # забыть удаляет записи, но не статистику
    assert hist.get_summary() == {"history_rows": 1, "users": 2, "total_messages": 2}
