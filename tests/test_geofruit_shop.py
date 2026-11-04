"""Покупки персоны и модальное окно магазина."""

import discord

from src.features.geofruit_shop import GeoFruitPersonaModal, SHOP_ITEMS


def test_persona_shop_lots_have_expected_prices_and_access():
    items = {item["id"]: item for item in SHOP_ITEMS}
    assert items["create_persona"]["price"] == 20
    assert items["edit_persona"]["price"] == 2
    assert items["create_persona"]["enabled"] is True
    assert items["edit_persona"]["enabled"] is True


def test_persona_modal_uses_multiline_labeled_text_input():
    item = next(item for item in SHOP_ITEMS if item["id"] == "create_persona")
    modal = GeoFruitPersonaModal(123, item, None)

    assert len(modal.children) == 1
    assert isinstance(modal.children[0], discord.ui.Label)
    assert modal.persona_input.style == discord.TextStyle.paragraph
    assert modal.persona_input.min_length == 10
    assert modal.persona_input.max_length == 500
    # Discord ограничивает placeholder для TextInput 100 символами.
    assert len(modal.persona_input.placeholder) <= 100
