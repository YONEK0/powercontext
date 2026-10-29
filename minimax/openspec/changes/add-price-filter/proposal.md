## Why

The book catalog currently supports free-text and category filters, but a
caller cannot ask for a price range. The SDD validation task needs one small,
testable behavior change that still preserves every existing query path.

## What Changes

- Add inclusive `min_price` and `max_price` query parameters to `GET /books`.
- Apply the price bounds after the existing `q` and `category` filters.
- Keep the existing result order and the unfiltered result unchanged.
- Add a target test and keep the existing tests as the regression gate.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `book-search`: add inclusive price bounds while preserving query, category
  and unfiltered behavior.

## Impact

- `demo_app/app.py`: `list_books` filtering logic.
- `demo_app/tests/test_books.py`: target and regression tests.
- `specs`/`openspec`: the effective requirement and the change delta.
- No new dependencies, database migration, pagination or sorting.

## Non-Goals

- Invalid ranges such as `min_price > max_price`.
- Sorting or pagination of filtered results.
- Authentication or persistence changes.
