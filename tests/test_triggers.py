"""Распознавание команд в тексте: арт и генерация картинки."""
import pytest

from src.utils.triggers import looks_like_foreign_command, match_trigger


@pytest.mark.parametrize(
    "text, expected",
    [
        ("арт", ("art", "")),
        ("арт лиса", ("art", "лиса")),
        ("Арт, волк в лесу", ("art", "волк в лесу")),
        ("найди арт лиса", ("art", "лиса")),
        ("art wolf", ("art", "wolf")),
        ("рисунок кота", ("art", "кота")),
        ("сгенерируй закат", ("gen", "закат")),
        ("ген", ("gen", "")),
        ("gen cat", ("gen", "cat")),
        ("создай картинку про осень", ("gen", "про осень")),
        ("сделай изображение дракона", ("gen", "дракона")),
    ],
)
def test_triggers_are_recognized(text, expected):
    assert match_trigger(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "артём привет",         # слово только НАЧИНАЕТСЯ с «арт»
        "артиллерия",
        "генерал кто это",
        "гениально",
        "сделай мне чаю",       # «сделай» без «картинку/изображение/рисунок»
        "создай историю про волка",
        "привет, как дела?",
        "",
    ],
)
def test_ordinary_messages_are_not_triggers(text):
    assert match_trigger(text) is None


@pytest.mark.parametrize("text", ["!play song", ".r d20", "/help", "/ген кошка", "-skip", ";p", "\\не отвечай", "\\", "  !play"])
def test_foreign_bot_commands_are_recognized(text):
    assert looks_like_foreign_command(text)


@pytest.mark.parametrize(
    "text",
    ["привет", ": привет", "- привет", "! привет", "...ну ладно", ":)", ";)", "!!!", "!", "-", "", "   ", "как дела?", "2+2"],
)
def test_ordinary_text_is_not_a_foreign_command(text):
    assert not looks_like_foreign_command(text)


def test_prefix_list_is_configurable(monkeypatch):
    from config import var

    monkeypatch.setattr(var, "ignored_prefixes", ("$",))
    assert looks_like_foreign_command("$bal")
    assert not looks_like_foreign_command("!play")
