"""Обрезка оборванных ответов ИИ."""
import pytest

from src.services.ai import Api


@pytest.fixture(scope="module")
def trim():
    return Api().trim_text


@pytest.mark.parametrize(
    "text",
    [
        "«Привет! Как дела»",           # закрывающая кавычка
        "Ну ладно, пойду спать 🦊",     # эмодзи в конце
        "Ну ладно… пойду спать ✨",
        "*фыркает*",
        "Всё хорошо...",
        "Привет!",
    ],
)
def test_finished_text_is_kept(trim, text):
    assert trim(text) == text


def test_unfinished_tail_is_cut_to_last_sentence(trim):
    assert trim("Хм. Это обрыв на полусло") == "Хм."


def test_text_without_any_sentence_end_is_kept(trim):
    assert trim("Просто ответ без точки") == "Просто ответ без точки"


def test_empty_and_spaces(trim):
    assert trim("") == ""
    assert trim("   ") == ""
