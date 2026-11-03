"""Диагностика бампов: описание сообщения, запись на время, сводка."""
import json

import pytest

from src.features import bump_sniffer as sniffer_module
from src.features.bump_sniffer import BumpSniffer, describe_message, format_diagnosis, to_json
from tests.real_messages import (
    BUMPER, CARL_BOT, COMPONENTS_V2, DISCODUS, LOADING, container, embed, make_real_message,
)

SUCCESS = "You've successfully bumped this server"


# ------------------------------------------------------------ describe_message

def test_describe_real_message_with_everything():
    message = make_real_message(
        content="контент",
        embeds=[embed(title="Заголовок", description="Описание", author="Автор", footer="Подвал", fields=[("Имя", "Значение")])],
        components=[container(SUCCESS)],
        flags=COMPONENTS_V2,
    )
    report = describe_message(message, raw={"сырые": "данные"})

    assert report["message_id"] == message.id
    assert report["author"] == {"id": CARL_BOT, "name": "Carl-bot", "bot": True}
    assert report["guild"]["id"] == 1000 and report["channel"]["name"] == "бампы"
    assert report["flags"] == ["components_v2"]
    assert report["interaction_metadata"]["user_id"] == BUMPER
    assert report["content"] == "контент"
    assert report["embeds"][0]["fields"] == [{"name": "Имя", "value": "Значение"}]
    assert report["embeds"][0]["footer"] == "Подвал" and report["embeds"][0]["author"] == "Автор"
    assert report["components_text"] == [SUCCESS]
    assert SUCCESS in report["text_seen_by_milky"]
    assert report["diagnosis"]["verdict"] == "reward"
    assert report["raw"] == {"сырые": "данные"}


def test_describe_shows_loading_flag_and_missing_metadata():
    report = describe_message(make_real_message(initiator=None, flags=LOADING))
    assert report["flags"] == ["loading"] and report["interaction_metadata"] is None
    assert report["diagnosis"]["verdict"] == "wait"
    assert "raw" not in report


def test_report_is_valid_json_with_cyrillic_kept_readable():
    text = to_json(describe_message(make_real_message(content="Привет, мир")))
    assert "Привет, мир" in text and "\\u" not in text
    assert json.loads(text)["content"] == "Привет, мир"


def test_long_texts_are_cut():
    report = describe_message(make_real_message(content="я" * 5000))
    assert len(report["content"]) <= 1500 and report["content"].endswith("…")


def test_format_diagnosis_marks_every_check():
    lines = format_diagnosis(describe_message(make_real_message(initiator=None, content="Time to bump"))["diagnosis"])
    assert lines[0].startswith("✅") and any(line.startswith("❌ Есть фраза успешного бампа") for line in lines)
    assert lines[-1].startswith("**Итог:**")


# ---------------------------------------------------------------- запись

def test_sniffer_is_off_by_default_and_turns_off_by_itself():
    sniffer = BumpSniffer()
    message = make_real_message(content=SUCCESS)
    assert sniffer.record("create", message, now=0) is False and sniffer.count() == 0

    sniffer.start(5, now=1000)
    assert sniffer.is_active(now=1299) and not sniffer.is_active(now=1300)          # ровно 5 минут
    assert sniffer.remaining(now=1100) == 200 and sniffer.remaining(now=5000) == 0
    assert sniffer.record("create", message, now=1100) is True
    assert sniffer.record("create", message, now=1301) is False                      # время вышло
    sniffer.stop()
    assert not sniffer.is_active(now=1100)


@pytest.mark.parametrize(
    "kwargs, self_id",
    [
        (dict(is_bot=False), None),                  # люди не записываются
        (dict(bot_id=999), 999),                     # сам Милки не записывается
        (dict(guild_id=777), None),                  # сервер без экономики
        (dict(guild_id=None), None),                 # личные сообщения
    ],
)
def test_sniffer_records_only_bots_on_economy_servers(kwargs, self_id):
    sniffer = BumpSniffer()
    sniffer.start(5, now=0)
    assert sniffer.record("create", make_real_message(content=SUCCESS, **kwargs), self_id=self_id, now=1) is False
    assert sniffer.count() == 0


def test_records_are_capped_and_newest_are_kept():
    sniffer = BumpSniffer(max_records=3)
    sniffer.start(5, now=0)
    ids = []
    for _ in range(5):
        message = make_real_message(content=SUCCESS)
        ids.append(message.id)
        sniffer.record("create", message, now=1)
    assert [r["message_id"] for r in sniffer.snapshot()] == ids[-3:]
    sniffer.clear()
    assert sniffer.count() == 0


def test_a_failure_while_describing_never_breaks_the_bot(monkeypatch):
    sniffer = BumpSniffer()
    sniffer.start(5, now=0)

    def broken(*args, **kwargs):
        raise RuntimeError("что-то пошло не так")

    monkeypatch.setattr(sniffer_module, "describe_message", broken)
    message = make_real_message(content=SUCCESS)
    assert sniffer.record("create", message, now=1) is True
    entry = sniffer.snapshot()[0]
    assert entry["message_id"] == message.id and "что-то пошло не так" in entry["error"]


def test_record_has_event_kind_and_timestamp():
    sniffer = BumpSniffer()
    sniffer.start(5, now=0)
    sniffer.record("edit", make_real_message(content=SUCCESS), raw={"a": 1}, now=1)
    entry = sniffer.snapshot()[0]
    assert entry["event"] == "edit" and entry["at"].endswith("+00:00") and entry["raw"] == {"a": 1}


# ---------------------------------------------------------------- сводка

def test_summary_groups_by_bot_and_shows_last_verdict():
    sniffer = BumpSniffer()
    sniffer.start(5, now=0)
    sniffer.record("create", make_real_message(flags=LOADING, initiator=None), now=1)               # Carl-bot «думает»
    sniffer.record("edit", make_real_message(content=SUCCESS, initiator=None), now=1)               # итог без инициатора
    sniffer.record("create", make_real_message(bot_id=DISCODUS, bot_name="Discodus", content="Bump done!"), now=1)

    carl, discodus = sniffer.summary_lines()
    assert f"**Carl-bot** (`{CARL_BOT}`)" in carl and "записей 2 (новых 1, правок 1)" in carl and "сбор реакций" in carl
    assert f"**Discodus** (`{DISCODUS}`)" in discodus and "записей 1" in discodus and "награда напрямую" in discodus
