"""Pure logic for watchlist notification triggers.

Kept separate from the Qt workers so it can be tested without a display or
network. The message conditions are the same ones used by MonitorWorker.
"""

from __future__ import annotations

from typing import Any

from steam_api import GameInfo, format_money


def notification_messages(
    game: GameInfo, previous: dict[str, Any] | None, entry: dict[str, Any], settings: Any
) -> list[str]:
    """Return notification strings for a watched game compared to its last state."""
    messages: list[str] = []
    if not previous:
        return messages

    old_price = previous.get("price")
    old_discount = int(previous.get("discount") or 0)
    currency = game.currency
    name = game.name

    if (
        settings.get("notify_discounts")
        and game.discount > 0
        and game.discount > old_discount
    ):
        messages.append(
            f"🔥 {name}: скидка {game.discount}% — {game.formatted_price}"
        )

    if (
        settings.get("notify_price_drop")
        and old_price is not None
        and game.price is not None
        and game.price < old_price
        and game.discount == old_discount
    ):
        messages.append(
            f"📉 {name}: цена {format_money(old_price, currency)} → {game.formatted_price}"
        )

    target = entry.get("target_price")
    if (
        settings.get("notify_target")
        and target is not None
        and game.price is not None
        and game.price <= int(target)
        and (old_price is None or old_price > int(target))
    ):
        messages.append(
            f"🎯 {name}: достигнута целевая цена {game.formatted_price}"
        )

    if game.free and not previous.get("price") == 0:
        messages.append(f"🆓 {name}: игра стала бесплатной")
    return messages
