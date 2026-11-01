"""Пути данных: список игнорируемых лежит в data/, старый файл переносится сам."""
from pathlib import Path

import config

OLD_CONTENT = '{"ignored_users": {"111111111111111111": {"nickname": "Старый бан"}}}'


def make_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "__file__", str(tmp_path / "config.py"))   # «корень проекта» во временной папке
    return config.Paths()


def test_ignored_users_live_in_data_folder(tmp_path, monkeypatch):
    paths = make_paths(tmp_path, monkeypatch)
    assert Path(paths.ignored_users) == tmp_path / "data" / "ignored_users.json"


def test_old_file_from_resources_is_copied_to_data(tmp_path, monkeypatch):
    (tmp_path / "resources").mkdir()
    old = tmp_path / "resources" / "ignored_users.json"
    old.write_text(OLD_CONTENT, encoding="utf-8")

    paths = make_paths(tmp_path, monkeypatch)
    assert Path(paths.ignored_users).read_text(encoding="utf-8") == OLD_CONTENT
    assert old.exists()                                  # исходник не удаляется: перенос безопасный


def test_existing_data_file_is_never_overwritten(tmp_path, monkeypatch):
    (tmp_path / "resources").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "resources" / "ignored_users.json").write_text(OLD_CONTENT, encoding="utf-8")
    current = tmp_path / "data" / "ignored_users.json"
    current.write_text('{"ignored_users": {}}', encoding="utf-8")

    make_paths(tmp_path, monkeypatch)
    assert current.read_text(encoding="utf-8") == '{"ignored_users": {}}'


def test_example_file_is_not_treated_as_the_real_list(tmp_path, monkeypatch):
    (tmp_path / "resources").mkdir()
    (tmp_path / "resources" / "ignored_users.example.json").write_text(OLD_CONTENT, encoding="utf-8")
    paths = make_paths(tmp_path, monkeypatch)
    assert not Path(paths.ignored_users).exists()
