"""Как Милки опознаёт бамп: тексты, инициатор, награда напрямую и решение (diagnose)."""
import warnings

import pytest

from config import var
from src.features import economy as economy_module
from src.features.economy import ECONOMY_KEY, collect_text, normalize_text
from tests.real_messages import (
    BUMPER, CARL_BOT, COMPONENTS_V2, DISBOARD, DISCODUS, GUILD, LOADING, container, embed, make_real_message,
)

SUCCESS = "You've successfully bumped this server"


# ------------------------------------------------------------------ нормализация

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("You’ve **successfully** bumped", "you've successfully bumped"),     # типографский апостроф и Markdown
        ("BUMP   DONE!", "bump done!"),
        ("a\n\nb\t c", "a b c"),
        ("<:ok:123456789012345678> Bump done", "bump done"),                  # серверное эмодзи
        ("<a:dance:123456789012345678>Готово", "готово"),
        ("__*~`|>text", "text"),
        ("", ""), (None, ""),
    ],
)
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


# --------------------------------------------------------- где искать текст

SUCCESS_VARIANTS = {
    "обычный текст": dict(content=SUCCESS),
    "типографский апостроф": dict(content="You’ve successfully bumped this server"),
    "Markdown": dict(content="You've **successfully** bumped this server"),
    "ЗАГЛАВНЫЕ и переносы": dict(content="YOU'VE SUCCESSFULLY\nBUMPED   THIS SERVER!"),
    "с серверным эмодзи": dict(content="<:ok:123456789012345678> You've successfully bumped this server!"),
    "описание эмбеда": dict(embeds=[embed(description=SUCCESS)]),
    "заголовок эмбеда": dict(embeds=[embed(title=SUCCESS)]),
    "поле эмбеда (field)": dict(embeds=[embed(title="Bump", fields=[("Результат", SUCCESS)])]),
    "подвал эмбеда": dict(embeds=[embed(description="x", footer=SUCCESS)]),
    "компоненты v2 (контейнер)": dict(components=[container("Привет", SUCCESS)], flags=COMPONENTS_V2),
    "компоненты v2 (секция)": dict(
        components=[{"type": 9, "components": [{"type": 10, "content": SUCCESS}],
                     "accessory": {"type": 2, "style": 1, "label": "Vote", "custom_id": "vote"}}],
        flags=COMPONENTS_V2,
    ),
}


@pytest.mark.parametrize("name", list(SUCCESS_VARIANTS))
@pytest.mark.parametrize("bot_id", [CARL_BOT, DISCODUS])
def test_success_is_recognized_wherever_the_text_is(eco, name, bot_id):
    message = make_real_message(bot_id=bot_id, initiator=None, **SUCCESS_VARIANTS[name])
    assert eco.matched_phrase(message) is not None, name
    assert eco.is_reaction_bump_success(message)


@pytest.mark.parametrize(
    "variant",
    [
        dict(content="Time to bump! Use /bump"),
        dict(content="Please wait 1 hour before bumping again"),
        dict(content="Bump failed: server not listed"),
        dict(embeds=[embed(title="Bump reminder", description="You can bump again in 2 hours")]),
        dict(components=[container("Next bump available soon")], flags=COMPONENTS_V2),
        dict(content=""),
    ],
)
def test_reminders_and_failures_are_not_success(eco, variant):
    message = make_real_message(initiator=None, **variant)
    assert eco.matched_phrase(message) is None
    assert not eco.is_reaction_bump_success(message)


def test_collect_text_reads_every_place_of_a_real_message():
    message = make_real_message(
        content="контент",
        embeds=[embed(title="заголовок", description="описание", author="автор", footer="подвал", fields=[("имя", "значение")])],
        components=[container("текст компонента")],
        flags=COMPONENTS_V2,
    )
    text = collect_text(message)
    for part in ("контент", "заголовок", "описание", "автор", "подвал", "имя", "значение", "текст компонента"):
        assert part in text


# -------------------------------------------------------------- инициатор

def test_initiator_from_interaction_metadata(eco):
    assert eco.find_initiator(make_real_message(content=SUCCESS)) == (BUMPER, "interaction_metadata")


def test_initiator_from_mention_is_a_weaker_source(eco):
    message = make_real_message(initiator=None, content=f"<@{BUMPER}> bumped. {SUCCESS}")
    assert eco.find_initiator(message) == (BUMPER, "mention")
    message = make_real_message(initiator=None, embeds=[embed(fields=[("Кто", f"<@!{BUMPER}>")])])
    assert eco.find_initiator(message) == (BUMPER, "mention")


def test_initiator_unknown(eco):
    assert eco.find_initiator(make_real_message(initiator=None, content=SUCCESS)) == (None, None)


def test_metadata_wins_over_mentions_in_text(eco):
    message = make_real_message(content="<@888000000000000001> " + SUCCESS)
    assert eco.find_initiator(message) == (BUMPER, "interaction_metadata")


# --------------------------------------------- награда напрямую для Carl-bot/Discodus

@pytest.mark.parametrize("bot_id", [CARL_BOT, DISCODUS])
def test_reaction_bots_reward_directly_when_discord_names_the_initiator(eco, bot_id):
    message = make_real_message(bot_id=bot_id, content=SUCCESS)
    assert eco.process_bump_message(message) == BUMPER
    assert eco.get_balance(BUMPER) == 1
    assert eco.process_bump_message(message) is None           # тот же бамп второй раз не награждается
    assert eco.get_balance(BUMPER) == 1
    assert eco.is_rewarded(message.id)


@pytest.mark.parametrize("bot_id", [CARL_BOT, DISCODUS])
def test_reaction_bots_without_known_initiator_go_to_minigame(eco, bot_id):
    message = make_real_message(bot_id=bot_id, initiator=None, content=SUCCESS)
    assert eco.process_bump_message(message) is None
    assert eco.is_reaction_bump_success(message) and not eco.is_rewarded(message.id)


def test_mention_only_is_not_trusted_for_reaction_bots(eco):
    message = make_real_message(initiator=None, content=f"<@{BUMPER}> {SUCCESS}")
    assert eco.process_bump_message(message) is None            # упоминание могло быть чьим угодно
    assert eco.get_balance(BUMPER) == 0


def test_mention_is_still_trusted_for_other_bots(eco):
    message = make_real_message(bot_id=DISBOARD, bot_name="DISBOARD", initiator=None, content=f"<@{BUMPER}> Bump done!")
    assert eco.process_bump_message(message) == BUMPER


def test_direct_reward_for_reaction_bots_can_be_switched_off(eco, monkeypatch):
    monkeypatch.setattr(economy_module, "DIRECT_REWARD_FOR_REACTION_BOTS", False)
    message = make_real_message(content=SUCCESS)
    assert eco.process_bump_message(message) is None
    assert eco.get_balance(BUMPER) == 0


def test_no_second_reward_path_when_minigame_already_started(eco):
    message = make_real_message(content=SUCCESS)
    assert eco.reserve_reaction_event(message)                  # сбор реакций уже запущен
    assert eco.has_reaction_event(message.id)
    assert eco.process_bump_message(message) is None            # прямая награда за него не нужна
    assert eco.get_balance(BUMPER) == 0


def test_other_bots_are_unchanged(eco):
    message = make_real_message(bot_id=DISBOARD, bot_name="DISBOARD", content="Bump done!")
    assert eco.process_bump_message(message) == BUMPER
    assert eco.get_balance(BUMPER) == 1


def test_economy_key_is_the_shared_balance(eco):
    eco.process_bump_message(make_real_message(content=SUCCESS))
    with eco._connect() as conn:
        assert conn.execute("SELECT guild_id FROM balances").fetchone()[0] == ECONOMY_KEY


# ------------------------------------------------------------------ diagnose

def verdict(eco, **kwargs):
    return eco.diagnose(make_real_message(**kwargs))["verdict"]


def test_diagnose_covers_every_decision(eco):
    assert verdict(eco, content=SUCCESS) == "reward"                                   # инициатор известен
    assert verdict(eco, initiator=None, content=SUCCESS) == "minigame"                 # неизвестен - сбор реакций
    assert verdict(eco, flags=LOADING, content="") == "wait"                           # «бот думает»
    assert verdict(eco, content="Time to bump!") == "ignore"                           # не успех
    assert verdict(eco, bot_id=123456789012345678, content=SUCCESS) == "ignore"        # бот не из списка
    assert verdict(eco, guild_id=777, content=SUCCESS) == "ignore"                     # не сервер экономики
    assert verdict(eco, is_bot=False, content=SUCCESS) == "ignore"                     # не бот
    assert verdict(eco, bot_id=DISBOARD, initiator=None, content="Bump done!") == "ignore"   # успех, но не знаем кому


def test_diagnose_explains_the_failed_check(eco):
    result = eco.diagnose(make_real_message(initiator=None, content="Server bumped!"))
    failed = [c for c in result["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["Есть фраза успешного бампа", "Известен инициатор бампа"]
    assert "server bumped!" in failed[0]["detail"]                                      # видно, что именно увидела Милки
    assert "Есть фраза успешного бампа" in result["summary"]


def test_diagnose_reports_source_and_mode(eco):
    checks = {c["name"]: c for c in eco.diagnose(make_real_message(content=SUCCESS))["checks"]}
    assert "interaction_metadata" in checks["Известен инициатор бампа"]["detail"] and str(BUMPER) in checks["Известен инициатор бампа"]["detail"]
    assert checks["Бот известен экономике"]["detail"] == "режим: сбор реакций"
    assert checks["Сервер с экономикой"]["detail"].startswith(f"сервер {GUILD}")


def test_diagnose_changes_nothing(eco):
    message = make_real_message(content=SUCCESS)
    for _ in range(3):
        eco.diagnose(message)
    assert eco.get_balance(BUMPER) == 0 and not eco.is_rewarded(message.id) and not eco.has_reaction_event(message.id)


def test_diagnose_follows_the_switch(eco, monkeypatch):
    monkeypatch.setattr(economy_module, "DIRECT_REWARD_FOR_REACTION_BOTS", False)
    assert verdict(eco, content=SUCCESS) == "minigame"
    assert var.economy_guild_ids                         # (настройка теста: экономика включена)


def test_deprecated_interaction_field_does_not_spam_warnings(eco):
    """discord.py сам принудительно показывает предупреждение об устаревшем message.interaction."""
    message = make_real_message(initiator=None, content=SUCCESS)         # метаданных нет: дойдём до запасного поля
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        eco.find_initiator(message)
        eco.diagnose(message)
    assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []
