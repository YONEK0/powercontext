## ADDED Requirements

### Requirement: Reservation respects stock

The service SHALL reject a reservation when the requested quantity exceeds the
free quantity, leaving the stored reservation unchanged.

#### Scenario: Over-reservation

- **WHEN** a caller reserves `1` unit of `SKU-200` with `on_hand=12` and
  `reserved=12`
- **THEN** the reservation SHALL be rejected
- **AND** the stored `reserved` value SHALL remain `12`

#### Scenario: Accepted reservation

- **WHEN** a caller reserves `3` units of `SKU-100` with `on_hand=40` and
  `reserved=5`
- **THEN** the reservation SHALL be accepted
- **AND** the stored `reserved` value SHALL become `8`
