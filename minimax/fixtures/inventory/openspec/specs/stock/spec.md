# stock Specification

## Purpose

Track warehouse stock and let callers reserve bolts, nuts and washers. The
service is a local library with an in-repo data file.

## Requirements

### Requirement: Free quantity

The service SHALL compute free quantity as `on_hand - reserved`.

#### Scenario: Free quantity

- **WHEN** an item has `on_hand=40` and `reserved=5`
- **THEN** the free quantity SHALL be `35`

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

### Requirement: Availability and low stock

Availability SHALL exclude fully reserved items, and the low-stock report SHALL
use free quantity rather than raw `on_hand`.

#### Scenario: Availability

- **WHEN** `SKU-200` has no free quantity
- **THEN** it SHALL NOT appear in the available list

#### Scenario: Low stock

- **WHEN** the threshold is `4`
- **THEN** the low-stock report SHALL contain `SKU-400` and `SKU-200`

### Requirement: Warehouse filter

Filtering by warehouse SHALL return only items from that warehouse.

#### Scenario: Warehouse filter

- **WHEN** the warehouse is `north`
- **THEN** two items SHALL be returned
