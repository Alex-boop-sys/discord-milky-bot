"""Серверы, каналы и ID из .env: разбор значений и значения по умолчанию."""
import pytest

from config import Var, _env_int, _env_int_list

ID_VARIABLES = ("MAIN_GUILD_ID", "ALLOWED_GUILD_IDS", "ECONOMY_GUILD_IDS", "GREETINGS_CHANNEL_IDS")


@pytest.fixture
def clean_env(monkeypatch):
    """Как будто в .env ни один ID не задан."""
    for name in ID_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    "raw, expected",
    [("111,222", [111, 222]), (" 1 , 2 , ", [1, 2]), ("5", [5]), ("", [9]), (",", [9])],
)
def test_env_int_list(monkeypatch, raw, expected):
    monkeypatch.setenv("TEST_LIST", raw)
    assert _env_int_list("TEST_LIST", [9]) == expected


def test_env_int_list_missing_variable(monkeypatch):
    monkeypatch.delenv("TEST_LIST", raising=False)
    assert _env_int_list("TEST_LIST", [9]) == [9]
    assert _env_int_list("TEST_LIST") == []


@pytest.mark.parametrize("raw", ["abc", "1,x", "12 34", "-5", "1;2"])
def test_typo_in_list_stops_the_bot_with_clear_message(monkeypatch, raw):
    monkeypatch.setenv("TEST_LIST", raw)
    with pytest.raises(ValueError, match="TEST_LIST"):
        _env_int_list("TEST_LIST", [9])


def test_env_int(monkeypatch):
    monkeypatch.setenv("TEST_ID", "123")
    assert _env_int("TEST_ID") == 123
    monkeypatch.setenv("TEST_ID", "")
    assert _env_int("TEST_ID") == 0
    monkeypatch.delenv("TEST_ID")
    assert _env_int("TEST_ID") == 0
    monkeypatch.setenv("TEST_ID", "abc")
    with pytest.raises(ValueError, match="TEST_ID"):
        _env_int("TEST_ID")


def test_nothing_set_means_no_ids_in_code(clean_env):
    var = Var()
    assert var.main_guild_id == 0
    assert var.allowed_guild_ids == []                 # ограничений нет
    assert var.economy_guild_ids == []                 # экономика нигде не включена
    assert var.morning_greetings_channel_ids == []     # приветствий нет


def test_main_guild_is_default_for_allowed_and_economy(clean_env):
    clean_env.setenv("MAIN_GUILD_ID", "111")
    var = Var()
    assert var.main_guild_id == 111
    assert var.allowed_guild_ids == [111]
    assert var.economy_guild_ids == [111]
    assert var.morning_greetings_channel_ids == []     # каналы по умолчанию не подставляются


def test_explicit_lists_override_defaults(clean_env):
    clean_env.setenv("MAIN_GUILD_ID", "111")
    clean_env.setenv("ALLOWED_GUILD_IDS", "111,222,333")
    clean_env.setenv("ECONOMY_GUILD_IDS", "111,222")
    clean_env.setenv("GREETINGS_CHANNEL_IDS", "5,6")
    var = Var()
    assert var.allowed_guild_ids == [111, 222, 333]
    assert var.economy_guild_ids == [111, 222]
    assert var.morning_greetings_channel_ids == [5, 6]


def test_cooldown_setting_is_a_non_negative_number(clean_env):
    assert Var().user_cooldown_seconds >= 0
