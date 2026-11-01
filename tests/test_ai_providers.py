"""Цепочка ИИ-провайдеров: выбор провайдера, запасные варианты и проверка для root# ai."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from config import env, var
from src.services.ai import Api, ProviderCheck, api
from src.utils.json_parser import dicts


@pytest.fixture
def providers(monkeypatch):
    """Подменяет четыре вызова провайдеров; возвращает словарь моков."""
    mocks = {name: AsyncMock(return_value=None) for name in ("cloudflare", "zai", "openrouter", "groq")}
    for name, mock in mocks.items():
        monkeypatch.setattr(api, f"_call_{name}", mock)
    return mocks


@pytest.fixture
def all_keys(monkeypatch):
    for attr in ("cloudflare_account_id", "cloudflare_api_token", "zai_api_key", "openrouter_api_key", "groq_api_key"):
        monkeypatch.setattr(env, attr, "ключ")


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ call_provider

@pytest.mark.parametrize("provider", ["cloudflare", "zai", "openrouter", "groq"])
def test_call_provider_routes_to_the_right_function(providers, provider):
    providers[provider].return_value = "ответ"
    assert run(api.call_provider(provider, "модель", [{"role": "user", "content": "hi"}])) == "ответ"
    providers[provider].assert_awaited_once_with("модель", [{"role": "user", "content": "hi"}])
    assert sum(mock.await_count for mock in providers.values()) == 1


def test_unknown_provider_returns_none_without_calls(providers):
    assert run(api.call_provider("несуществующий", "m", [])) is None
    assert sum(mock.await_count for mock in providers.values()) == 0


# ------------------------------------------------------------------ ask_ai

def test_ask_ai_falls_back_to_the_next_provider(providers, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("cloudflare", "m1"), ("zai", "m2"), ("groq", "m3")])
    providers["cloudflare"].return_value = None
    providers["zai"].side_effect = RuntimeError("сбой сети")          # исключение тоже не страшно
    providers["groq"].return_value = "Привет из запасного!"

    assert run(api.ask_ai(900001, "Алекс", "привет")) == "Привет из запасного!"
    assert providers["cloudflare"].await_count == providers["zai"].await_count == providers["groq"].await_count == 1


def test_ask_ai_stops_at_first_working_provider(providers, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("cloudflare", "m1"), ("groq", "m3")])
    providers["cloudflare"].return_value = "Первый ответил."
    assert run(api.ask_ai(900002, "Алекс", "привет")) == "Первый ответил."
    providers["groq"].assert_not_called()


def test_ask_ai_when_everything_is_down(providers, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("cloudflare", "m1"), ("groq", "m3"), ("странный", "m4")])
    assert run(api.ask_ai(900003, "Алекс", "привет")) == dicts.get("api_failure")


def test_ask_standalone_uses_the_same_chain(providers, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("cloudflare", "m1"), ("groq", "m3")])
    providers["groq"].return_value = "*фыркает* ответ"
    assert run(api.ask_standalone("вопрос")) == "\\*фыркает\\* ответ"


# ---------------------------------------------------------- has_credentials

def test_has_credentials(monkeypatch):
    for attr in ("cloudflare_account_id", "cloudflare_api_token", "zai_api_key", "openrouter_api_key", "groq_api_key"):
        monkeypatch.setattr(env, attr, None)
    assert not any(Api.has_credentials(p) for p in ("cloudflare", "zai", "openrouter", "groq", "другой"))

    monkeypatch.setattr(env, "cloudflare_account_id", "аккаунт")
    assert not Api.has_credentials("cloudflare")                     # нужны оба значения
    monkeypatch.setattr(env, "cloudflare_api_token", "токен")
    assert Api.has_credentials("cloudflare")
    monkeypatch.setattr(env, "groq_api_key", "ключ")
    assert Api.has_credentials("groq") and not Api.has_credentials("zai")


# ------------------------------------------------------------ check_providers

def test_check_providers_reports_each_provider(providers, all_keys, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("cloudflare", "a"), ("zai", "b"), ("groq", "c"), ("openrouter", "d")])
    monkeypatch.setattr(env, "zai_api_key", None)                    # у zai ключа нет
    providers["cloudflare"].return_value = "ок"
    providers["groq"].return_value = None                             # ответа нет
    providers["openrouter"].side_effect = RuntimeError("упал")        # исключение

    results = run(api.check_providers())
    assert [(r.provider, r.status) for r in results] == [
        ("cloudflare", "ok"), ("zai", "no_key"), ("groq", "fail"), ("openrouter", "fail"),
    ]
    assert all(isinstance(r, ProviderCheck) and r.seconds >= 0 for r in results)
    providers["zai"].assert_not_called()                              # без ключа запрос не тратим


def test_blank_answer_counts_as_failure(providers, all_keys, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("groq", "c")])
    providers["groq"].return_value = "   "
    assert run(api.check_providers())[0].status == "fail"


def test_providers_are_checked_in_parallel(all_keys, monkeypatch):
    """Каждый запрос ждёт, пока стартуют все остальные: при последовательном опросе тест завис бы."""
    names = ("cloudflare", "zai", "groq")
    monkeypatch.setattr(var, "fallback_models", [(n, "m") for n in names])

    async def scenario():
        started = asyncio.Event()
        count = 0

        async def waiting_call(model, messages):
            nonlocal count
            count += 1
            if count == len(names):
                started.set()
            await started.wait()
            return "ок"

        for name in names:
            monkeypatch.setattr(api, f"_call_{name}", waiting_call)
        return await asyncio.wait_for(api.check_providers(), timeout=2)

    assert [r.status for r in asyncio.run(scenario())] == ["ok", "ok", "ok"]


def test_check_does_not_touch_history(providers, all_keys, monkeypatch):
    monkeypatch.setattr(var, "fallback_models", [("groq", "c")])
    providers["groq"].return_value = "ок"
    before = api.history.get_summary()
    run(api.check_providers())
    assert api.history.get_summary() == before
