"""Проверка необязательных ID владельцев из .env."""

import pytest

from config import Env


def test_owner_ids_are_optional_and_deduplicated(monkeypatch):
    monkeypatch.delenv("OWNER_ID", raising=False)
    monkeypatch.delenv("CO_OWNER_ID", raising=False)
    monkeypatch.setenv("CO_OWNER_ID", "123")
    assert Env().owner_ids == [123]

    monkeypatch.setenv("OWNER_ID", "123")
    assert Env().owner_ids == [123]


@pytest.mark.parametrize("name", ["OWNER_ID", "CO_OWNER_ID"])
def test_invalid_owner_id_fails_with_variable_name(monkeypatch, name):
    monkeypatch.delenv("OWNER_ID", raising=False)
    monkeypatch.delenv("CO_OWNER_ID", raising=False)
    monkeypatch.setenv(name, "not-a-discord-id")
    with pytest.raises(ValueError, match=name):
        Env()
