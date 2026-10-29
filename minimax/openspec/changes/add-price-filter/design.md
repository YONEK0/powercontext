## Context

`demo_app/app.py` loads a fixed `books.json` catalog and filters it in
`list_books`. The initial implementation already exposes the two price
parameters in its signature but does not apply them. The change is deliberately
local: no persistence, pagination or sorting.

## Goals / Non-Goals

- Goal: enforce inclusive lower/upper price bounds.
- Goal: keep `q`, `category`, the unfiltered result and the original order.
- Non-goal: sorting filtered results.
- Non-goal: rejecting an inverted range; it is outside this validation sample.

## Decisions

1. Apply price bounds after the existing filters, so `q` and `category` keep
   their current semantics and all filters compose.
2. Use `price >= min_price` and `price <= max_price`; both comparisons are
   inclusive by construction.
3. Do not sort. The JSON file order is the existing observable order and the
   regression test protects it.

## Risks / Trade-offs

- A caller that expects sorted prices must sort client-side; this is explicit
  in the spec and preserves the current behavior.
- Inverted ranges return an empty list rather than an error; that is a
  documented non-goal.
