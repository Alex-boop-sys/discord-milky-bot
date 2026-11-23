import sqlite3

import pytest

from config import var
from src.services.lorebook import LorebookService, _contains_trigger, normalize_trigger


def test_trigger_matching_uses_whole_words_and_casefold():
    assert _contains_trigger("I met VELVET today", "velvet")
    assert _contains_trigger("говорили о ВЕЛЬВЕТ", "вельвет")
    assert _contains_trigger("SAW-11 появился", "saw-11")
    assert not _contains_trigger("velvetine", "velvet")
    assert not _contains_trigger("x-saw-11x", "saw-11")
    assert normalize_trigger(" ВЕЛЬВЕТ ") == "вельвет"


def test_initial_entries_are_seeded_and_searchable(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", True)
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        rows = service.conn.execute(
            "SELECT title FROM lore_entries ORDER BY id"
        ).fetchall()
        assert [row[0] for row in rows] == [
            "Вельвет (SAW-11)",
            "Вояджер (SAW-07)",
        ]

        context = service.build_context("Расскажи про velvet")
        assert "Вельвет (SAW-11)" in context
        assert "Вояджер" not in context
    finally:
        service.close()


def test_duplicate_normalized_trigger_is_rejected(tmp_path):
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        entry_id = service.conn.execute(
            "SELECT id FROM lore_entries LIMIT 1"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            with service.conn:
                service.conn.execute(
                    """
                    INSERT INTO lore_triggers
                        (entry_id, trigger_text, normalized_trigger)
                    VALUES (?, ?, ?)
                    """,
                    (entry_id, "VELVET", "velvet"),
                )
    finally:
        service.close()


def test_recent_mention_suppresses_entire_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", True)
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        context = service.build_context(
            "Что думаешь о Velvet?",
            earlier_messages=[{"role": "user", "content": "Игрок: saw-11"}],
        )
        assert context is None
    finally:
        service.close()


def test_previous_assistant_can_trigger_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", True)
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        context = service.build_context(
            "А что насчёт второго?",
            previous_assistant="Вельвет умеет плести паутину.",
        )
        assert context is not None
        assert "Вельвет (SAW-11)" in context
    finally:
        service.close()


def test_entry_and_character_limits_are_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", True)
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        context = service.build_context(
            "velvet and voyager", max_entries=1, max_chars=6000
        )
        assert context is not None
        assert len(context) <= 6000
        assert ("Вельвет" in context) != ("Вояджер" in context)
    finally:
        service.close()


def test_disabled_lorebook_returns_no_context(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", False)
    service = LorebookService(str(tmp_path / "lorebook.db"))
    try:
        assert service.build_context("velvet") is None
    finally:
        service.close()


def test_seed_is_idempotent_and_priority_selects_higher_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(var, "lorebook_enabled", True)
    db_path = str(tmp_path / "lorebook.db")
    service = LorebookService(db_path)
    try:
        with service.conn:
            service.conn.execute(
                "UPDATE lore_entries SET priority = 90 WHERE title = ?",
                ("Вояджер (SAW-07)",),
            )
        service.close()

        service = LorebookService(db_path)
        rows = service.conn.execute(
            "SELECT COUNT(*) FROM lore_entries"
        ).fetchone()[0]
        assert rows == 2

        context = service.build_context("velvet and voyager", max_entries=1)
        assert "Вояджер (SAW-07)" in context
        assert "Вельвет (SAW-11)" not in context
    finally:
        service.close()
