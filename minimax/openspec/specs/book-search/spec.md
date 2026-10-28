# book-search Specification

## Purpose

Expose a read-only catalog query for the MiniMax SDD validation task. The
service returns books from the bundled data file and supports query, category
and (after the change) inclusive price bounds.

## Requirements

### Requirement: Inclusive price bounds

The service SHALL filter books by an inclusive `min_price` / `max_price`
range when the corresponding query parameter is supplied.

#### Scenario: Lower and upper bounds

- **WHEN** the caller requests `/books?min_price=10&max_price=20`
- **THEN** every returned book SHALL have `price >= 10` and `price <= 20`
- **AND** the bounds SHALL be inclusive
- **AND** the matching books SHALL remain in their original data order

#### Scenario: Only one bound

- **WHEN** only `min_price` or only `max_price` is supplied
- **THEN** only that bound SHALL be applied

### Requirement: Existing query behavior

The service SHALL preserve the existing `q` and `category` filters and SHALL
combine them with price bounds when both are supplied.

#### Scenario: Query and category regression

- **WHEN** the caller requests `/books?q=ada` or `/books?category=fiction`
- **THEN** the result SHALL match the pre-change behavior

### Requirement: Unfiltered result

The service SHALL keep the existing six-book result and its order when no
query parameters are supplied.

#### Scenario: Unfiltered list

- **WHEN** the caller requests `/books`
- **THEN** the response SHALL contain the same six books in the same order

### Requirement: Validation evidence

The change SHALL provide a target test that fails before implementation and
passes afterwards, plus regression tests for the existing behavior.

#### Scenario: Target and regression gates

- **WHEN** the target test `test_price_filter_is_inclusive_and_keeps_order` runs
- **THEN** it SHALL fail against the initial code and pass after the change
- **AND** the other tests in `demo_app/tests/test_books.py` SHALL pass
