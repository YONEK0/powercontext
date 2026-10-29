## ADDED Requirements

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
