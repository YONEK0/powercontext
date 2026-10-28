## 1. Context and specification

- [x] 1.1 Read the effective `book-search` specification and the change delta.
- [x] 1.2 Select `demo_app/app.py#list_books` and the target/regression tests.

## 2. Implementation

- [x] 2.1 Apply `min_price` and `max_price` after the existing filters.
- [x] 2.2 Preserve the original JSON order; do not add sorting.

## 3. Verification

- [x] 3.1 Target test fails before the change and passes after it.
- [x] 3.2 `test_query_filter_still_works`, `test_category_filter_still_works`
  and `test_unfiltered_list_is_unchanged` remain green.
