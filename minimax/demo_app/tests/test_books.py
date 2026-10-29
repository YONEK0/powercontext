from __future__ import annotations

from fastapi.testclient import TestClient

from demo_app.app import app

client = TestClient(app)


def test_price_filter_is_inclusive_and_keeps_order() -> None:
    response = client.get("/books", params={"min_price": 10, "max_price": 20})
    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "the demo data contains books in the inclusive 10..20 range"
    prices = [book["price"] for book in items]
    assert all(10 <= price <= 20 for price in prices)
    assert prices == [18.0, 12.0]


def test_query_filter_still_works() -> None:
    response = client.get("/books", params={"q": "ada"})
    assert response.status_code == 200
    titles = {book["title"] for book in response.json()["items"]}
    assert titles == {"Database Systems", "Systems at Scale"}


def test_category_filter_still_works() -> None:
    response = client.get("/books", params={"category": "fiction"})
    assert response.status_code == 200
    titles = {book["title"] for book in response.json()["items"]}
    assert titles == {"The Long River", "Paper Moon"}


def test_unfiltered_list_is_unchanged() -> None:
    response = client.get("/books")
    assert response.status_code == 200
    assert response.json()["count"] == 6
