"""Small book-search business service used by the SDD validation task."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI

_DATA = Path(__file__).with_name("data") / "books.json"
BOOKS: list[dict[str, Any]] = json.loads(_DATA.read_text(encoding="utf-8"))

app = FastAPI(title="MiniMax SDD demo books")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/books")
def list_books(
    q: str | None = None,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> dict[str, Any]:
    items = BOOKS
    if q:
        needle = q.casefold()
        items = [
            book
            for book in items
            if needle in book["title"].casefold() or needle in book["author"].casefold()
        ]
    if category:
        items = [book for book in items if book["category"].casefold() == category.casefold()]
    if min_price is not None:
        items = [book for book in items if book["price"] >= min_price]
    if max_price is not None:
        items = [book for book in items if book["price"] <= max_price]
    return {"items": items, "count": len(items)}
