from __future__ import annotations

from stock.service import ITEMS, available, free_quantity, list_stock, low_stock, reserve


def test_reserve_rejects_quantity_above_free_stock() -> None:
    # Acceptance: reserving more than the free quantity must be rejected and
    # must not change the stored reservation.
    result = reserve("SKU-200", 1)
    assert result["accepted"] is False
    assert result["reserved"] == 12


def test_reserve_accepts_available_quantity() -> None:
    result = reserve("SKU-100", 3)
    assert result["accepted"] is True
    assert result["reserved"] == 8


def test_available_excludes_fully_reserved_items() -> None:
    skus = {item["sku"] for item in available(ITEMS)}
    assert "SKU-200" not in skus
    assert "SKU-100" in skus


def test_low_stock_uses_free_quantity() -> None:
    skus = {item["sku"] for item in low_stock(ITEMS, 4)}
    assert skus == {"SKU-400", "SKU-200"}


def test_warehouse_filter_is_unchanged() -> None:
    result = list_stock("north")
    assert result["count"] == 2


def test_free_quantity_is_on_hand_minus_reserved() -> None:
    assert free_quantity(ITEMS[0]) == 35
