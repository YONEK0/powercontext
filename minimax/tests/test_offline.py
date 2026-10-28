"""Offline checks: tokenizer, task resolution, patch normalization.

These do not need seekdb, so they run on any platform and cover the parts that
must stay correct independently of the storage backend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_context.agent import extract_patch, repair_hunk_counts
from minimax_context.config import resolve_python
from minimax_context.db import tokenize
from minimax_context.task import describe, resolve

ROOT = Path(__file__).resolve().parents[1]


def test_identifier_parts_are_indexed() -> None:
    terms = tokenize("max_price list_books HTTPError")
    assert "max_price" in terms
    assert "price" in terms
    assert "list" in terms
    assert "books" in terms
    assert "http" in terms


def test_cjk_text_produces_overlapping_bigrams() -> None:
    terms = tokenize("为图书检索增加价格区间过滤")
    assert "价格" in terms
    assert "图书" in terms
    assert "过滤" in terms


def test_task_comes_from_the_change_not_a_constant() -> None:
    task, change = resolve(ROOT)
    assert change == "add-price-filter"
    assert "min_price" in task
    assert task != "Add inclusive min_price/max_price filtering to GET /books"


def test_change_description_reads_the_proposal() -> None:
    text = describe(ROOT, "add-price-filter")
    assert "min_price" in text
    assert len(text) > 40


def test_missing_change_is_reported() -> None:
    with pytest.raises(FileNotFoundError):
        resolve(ROOT, change="does-not-exist")


def test_placeholder_interpreter_falls_back() -> None:
    assert resolve_python("/path/to/python-with-fastapi") == resolve_python(None)
    assert resolve_python("definitely-not-a-real-binary") == resolve_python(None)


def test_repair_rewrites_stale_hunk_counts_and_drops_index() -> None:
    patch = (
        "diff --git a/x.py b/x.py\n"
        "index abc123..000000000000 100644\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,9 +1,2 @@\n"
        " one\n"
        "+two\n"
        " three\n"
    )
    fixed = repair_hunk_counts(patch)
    assert "@@ -1,2 +1,3 @@" in fixed
    assert "index " not in fixed


def test_extract_patch_accepts_fenced_output() -> None:
    content = "```diff\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```"
    patch = extract_patch(content)
    assert patch.startswith("diff --git")
    assert patch.endswith("\n")


def test_extract_patch_rejects_prose_only() -> None:
    with pytest.raises(ValueError):
        extract_patch("I could not complete this task.")


def test_committed_fixture_data_exists() -> None:
    # Guards against .gitignore swallowing the fixture data again.
    assert (ROOT / "demo_app" / "data" / "books.json").is_file()
    assert (ROOT / "fixtures" / "inventory" / "stock" / "stock.json").is_file()


def test_committed_code_is_the_unimplemented_initial_state() -> None:
    """The repository must ship the *before* code.

    If the fixed version is committed, a judge cloning the repo finds the
    acceptance test already green and "fails before the change" is no longer
    reproducible.
    """

    shipped = (ROOT / "demo_app" / "app.py").read_text(encoding="utf-8")
    before = (ROOT / "evidence" / "before" / "app.py").read_text(encoding="utf-8")
    after = (ROOT / "evidence" / "after" / "app.py").read_text(encoding="utf-8")

    assert shipped == before, "demo_app/app.py must match evidence/before/app.py"
    assert shipped != after
    assert "min_price is not None" not in shipped
    assert "min_price is not None" in after

