import pytest

from notifications import notification_messages
from steam_api import GameInfo, format_money


def make_settings(**overrides):
    settings = {
        "notify_discounts": True,
        "notify_price_drop": True,
        "notify_target": True,
        "notify_releases": True,
    }
    settings.update(overrides)
    return settings


def make_game(*, price=500, initial=1000, discount=50, free=False):
    return GameInfo(
        appid=1,
        name="Sample",
        price=price,
        initial=initial,
        discount=discount,
        free=free,
        currency="RUB",
        formatted_price=format_money(price, "RUB"),
    )


def test_no_previous_state_never_notifies():
    assert notification_messages(make_game(), None, {}, make_settings()) == []


def test_discount_increase_notifies_only_when_enabled():
    game = make_game(discount=60, price=400)
    previous = {"price": 500, "discount": 50}
    messages = notification_messages(game, previous, {}, make_settings())
    assert len(messages) == 1
    assert "скидка 60%" in messages[0]

    assert notification_messages(
        game, previous, {}, make_settings(notify_discounts=False)
    ) == []


def test_price_drop_requires_same_discount():
    game = make_game(discount=0, price=400, initial=1000)
    previous = {"price": 500, "discount": 0}
    messages = notification_messages(game, previous, {}, make_settings())
    assert len(messages) == 1
    assert "цена" in messages[0]
    assert "4 ₽" in messages[0]

    changed_discount = make_game(discount=10, price=400)
    settings = make_settings(notify_discounts=False)
    assert notification_messages(changed_discount, previous, {}, settings) == []
    assert notification_messages(
        game, previous, {}, make_settings(notify_price_drop=False)
    ) == []


@pytest.mark.parametrize(
    ("old_price", "expected"),
    [(600, 1), (500, 0), (400, 0), (None, 1)],
)
def test_target_price_only_crosses_once(old_price, expected):
    game = make_game(discount=0, price=500, initial=1000)
    previous = {"price": old_price, "discount": 0}
    settings = make_settings(notify_discounts=False, notify_price_drop=False)
    messages = notification_messages(game, previous, {"target_price": 500}, settings)
    assert len(messages) == expected


def test_target_notification_can_be_disabled():
    game = make_game(discount=0, price=500)
    previous = {"price": 600, "discount": 0}
    settings = make_settings(notify_target=False, notify_price_drop=False)
    assert notification_messages(game, previous, {"target_price": 500}, settings) == []


def test_free_transition_notifies_only_from_paid():
    game = make_game(price=0, initial=0, discount=0, free=True)
    paid = {"price": 100, "discount": 0}
    already_free = {"price": 0, "discount": 0}
    settings = make_settings(notify_price_drop=False)
    assert "бесплатной" in notification_messages(game, paid, {}, settings)[0]
    assert notification_messages(game, already_free, {}, settings) == []
