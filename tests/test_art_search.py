"""Проверки совместимости с форматами ответа e621."""

from src.features.art_search import normalize_posts


def test_normalize_posts_supports_legacy_response():
    post = {"id": 1, "file": {"url": "https://example.test/old.jpg", "size": 123}}
    assert normalize_posts({"posts": [post]}) == [post]


def test_normalize_posts_supports_direct_list_and_new_file_format():
    post = {
        "id": 2,
        "files": {
            "original": {"url": "https://example.test/new.png", "size": 456},
            "meta": {"size": 456, "ext": "png"},
        },
    }

    result = normalize_posts([post])

    assert len(result) == 1
    assert result[0]["file"] == {
        "url": "https://example.test/new.png",
        "size": 456,
        "ext": "png",
    }


def test_normalize_posts_supports_new_format_inside_posts_wrapper():
    post = {
        "id": 3,
        "files": {
            "original": {"url": "https://example.test/wrapped.webp"},
            "meta": {"size": 789, "ext": "webp"},
        },
    }

    result = normalize_posts({"posts": [post]})

    assert result[0]["file"]["url"] == "https://example.test/wrapped.webp"
    assert result[0]["file"]["size"] == 789


def test_normalize_posts_ignores_invalid_records():
    assert normalize_posts([None, "bad", {"id": 4}]) == []
    assert normalize_posts({"posts": "not a list"}) == []
