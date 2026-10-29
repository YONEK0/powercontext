"""Stock reservation service — second fixture project.

Deliberately unrelated to the book-search demo: different domain, different
package name and its own change spec. It exists so the framework tests can prove
that pointing the module at another repository works without editing retrieval
code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA = Path(__file__).with_name("stock.json")
ITEMS: list[dict[str, Any]] = json.loads(_DATA.read_text(encoding="utf-8"))


def free_quantity(item: dict[str, Any]) -> int:
    return int(item["on_hand"]) - int(item["reserved"])


def available(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if free_quantity(item) > 0]


def low_stock(items: list[dict[str, Any]], threshold: int) -> list[dict[str, Any]]:
    return [item for item in items if free_quantity(item) < threshold]


def list_stock(warehouse: str | None = None) -> dict[str, Any]:
    items = ITEMS
    if warehouse:
        items = [item for item in items if item["warehouse"] == warehouse]
    return {"items": items, "count": len(items)}


def reserve(sku: str, quantity: int) -> dict[str, Any]:
    for item in ITEMS:
        if item["sku"] != sku:
            continue
        return {"sku": sku, "reserved": item["reserved"], "accepted": True}
    return {"sku": sku, "reserved": 0, "accepted": False}
