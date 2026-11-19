"""Проверки словаря реплик resources/dicts.json."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DICTS = json.loads((ROOT / "resources" / "dicts.json").read_text(encoding="utf-8"))

# Ищет в коде вызовы вида dicts.get("ключ") и dicts.get_random("ключ").
KEY_RE = re.compile(r"""dicts\.(?:get|get_random)\(\s*['"]([^'"]+)['"]""")


def test_every_key_used_in_code_exists_in_dicts():
    """Ловит опечатки в ключах (например, forgot_/forget_history_clear)."""
    missing = {}
    for path in (ROOT / "src").rglob("*.py"):
        for key in KEY_RE.findall(path.read_text(encoding="utf-8")):
            if key not in DICTS:
                missing.setdefault(key, []).append(path.name)
    assert not missing, f"Ключи есть в коде, но нет в dicts.json: {missing}"


def test_nsfw_patterns_are_valid_regex():
    for pattern in DICTS["nsfw_patterns"]:
        re.compile(pattern)


def test_random_reply_lists_are_not_empty():
    # get_random берёт случайный элемент списка: пустой список вызвал бы ошибку.
    for key, value in DICTS.items():
        if isinstance(value, list):
            assert value, f"список {key} пуст"


def test_cooldown_notice_has_seconds_placeholder():
    assert "{seconds}" in DICTS["cooldown_notice"]
    assert "3" in DICTS["cooldown_notice"].format(seconds=3)


def test_character_phrase_templates_have_expected_placeholders():
    assert "{mention}" in DICTS["bump_reward_announcement"]
    assert "{user_name}" in DICTS["shop_treat_fallback"]
    assert "{image_url}" in DICTS["art_large_image_link"]
    assert "{display_prompt}" in DICTS["image_generation_success"]
    assert "{remaining}" in DICTS["reaction_event_countdown"]


def test_morning_greeting_prompt_collections_are_complete():
    assert len(DICTS["morning_moods"]) == 7
    assert len(DICTS["morning_scenes"]) == 9
    assert len(DICTS["morning_styles"]) == 6
    assert set(DICTS["morning_weekday_notes"]) == {str(day) for day in range(7)}
    assert all(len(notes) == 2 for notes in DICTS["morning_weekday_notes"].values())
