"""Таймер «не чаще одного обращения за N секунд» (время подставляем вручную)."""
import pytest

from config import env, var
from src.utils.cooldown import Cooldown


@pytest.fixture(autouse=True)
def five_seconds(monkeypatch):
    monkeypatch.setattr(var, "user_cooldown_seconds", 5)
    monkeypatch.setattr(env, "owner_ids", [])


def test_first_request_is_allowed():
    assert Cooldown().check(1, now=100) == (True, 0.0, False)


def test_second_request_is_blocked_and_warned_once():
    cd = Cooldown()
    cd.check(1, now=100)
    assert cd.check(1, now=102) == (False, 3.0, True)    # осталось 3 секунды, предупреждаем
    assert cd.check(1, now=103) == (False, 2.0, False)   # второй раз уже молчим


def test_allowed_again_after_timer_ends():
    cd = Cooldown()
    cd.check(1, now=100)
    cd.check(1, now=101)                                  # заблокирован и предупреждён
    assert cd.check(1, now=105)[0] is True                # ровно через 5 секунд
    assert cd.check(1, now=106) == (False, 4.0, True)     # новый таймер - предупреждение снова


def test_users_do_not_affect_each_other():
    cd = Cooldown()
    cd.check(1, now=100)
    assert cd.check(2, now=100)[0] is True


def test_zero_disables_cooldown(monkeypatch):
    monkeypatch.setattr(var, "user_cooldown_seconds", 0)
    cd = Cooldown()
    assert cd.check(1, now=100)[0] is True
    assert cd.check(1, now=100)[0] is True


def test_bot_owners_are_exempt(monkeypatch):
    monkeypatch.setattr(env, "owner_ids", [1])
    cd = Cooldown()
    assert cd.check(1, now=100)[0] is True
    assert cd.check(1, now=100)[0] is True


def test_expired_entries_are_cleaned_up():
    cd = Cooldown()
    for user_id in range(1100):
        cd.check(user_id, now=0)
    cd.check(5000, now=100)                               # к этому моменту все старые таймеры истекли
    assert len(cd._users) < 10
