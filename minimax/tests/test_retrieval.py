"""Retrieval and context tests over seekdb.

The second half points the same code at `fixtures/inventory`, a project with a
different domain, package layout and language, and asserts the same API returns
a usable plan. That is the regression guard against re-specialising this module
for one ticket.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from minimax_context.context import build_context, context_from_plan
from minimax_context.db import SeekDB
from minimax_context.ingest import collect_docs, ingest, project_roots
from minimax_context.retrieve import build_plan, task_terms

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "fixtures" / "inventory"
BOOK_CHANGE = "add-price-filter"
BOOK_TASK = (
    "Add inclusive min_price/max_price filtering to GET /books without changing "
    "the existing q/category behavior"
)


@pytest.fixture()
def db(tmp_path: Path) -> SeekDB:
    handle = SeekDB(tmp_path / "seekdb", log_path=tmp_path / "queries.jsonl")
    yield handle
    handle.close()


def test_ingest_finds_every_project_directory() -> None:
    roots = project_roots(ROOT)
    names = {path.name for path in roots}
    assert "demo_app" in names
    # fixtures/ holds a second project; discovery must not hard-code demo_app
    assert "inventory" not in names or "fixtures" in names
    # the framework package itself must never be ingested as business code
    assert "minimax_context" not in names


def test_book_task_selects_all_three_kinds(db: SeekDB) -> None:
    counts = ingest(ROOT, db)
    assert counts["spec"] >= 1 and counts["code"] >= 1 and counts["test"] >= 1

    plan = build_plan(db, BOOK_TASK)
    assert plan.groups["spec"] and plan.groups["code"] and plan.groups["test"]
    assert any("openspec/specs/book-search/spec.md" == item.doc.path for item in plan.groups["spec"])
    assert any("demo_app/app.py" in item.doc.path for item in plan.groups["code"])
    assert plan.target_files and "demo_app/app.py" in plan.target_files


def test_selection_is_ranked_by_idf_not_by_keyword_table(db: SeekDB) -> None:
    ingest(ROOT, db)
    plan = build_plan(db, BOOK_TASK)
    scores = [item.score for item in plan.groups["test"]]
    assert scores == sorted(scores, reverse=True), "test bucket must be ordered by score"
    target = plan.candidate_tests[0]
    assert target.endswith("test_price_filter_is_inclusive_and_keeps_order")
    # every selected item records why it matched
    assert all(item.matched_terms for item in plan.items)


def test_changed_spec_changes_the_selected_version(tmp_path: Path, db: SeekDB) -> None:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "demo_app", project / "demo_app")
    shutil.copytree(ROOT / "openspec", project / "openspec")
    ingest(project, db)
    before = build_plan(db, BOOK_TASK).groups["spec"]
    assert before

    spec = project / "openspec" / "changes" / BOOK_CHANGE / "specs" / "book-search" / "spec.md"
    spec.write_text(spec.read_text(encoding="utf-8") + "\nRequirement: rounded bounds only.\n", encoding="utf-8")
    ingest(project, db)
    after = build_plan(db, BOOK_TASK).groups["spec"]

    changed = "openspec/changes/add-price-filter/specs/book-search/spec.md"
    old = {item.doc.path: item.doc.version for item in before}
    new = {item.doc.path: item.doc.version for item in after}
    assert changed in old and changed in new
    assert old[changed] != new[changed]
    assert any("rounded bounds only" in item.doc.content for item in after)


def test_removed_spec_disappears_from_selection(tmp_path: Path, db: SeekDB) -> None:
    project = tmp_path / "project"
    shutil.copytree(ROOT / "demo_app", project / "demo_app")
    shutil.copytree(ROOT / "openspec", project / "openspec")
    ingest(project, db)
    shutil.rmtree(project / "openspec" / "changes" / BOOK_CHANGE)
    ingest(project, db)
    paths = {item.doc.path for item in build_plan(db, BOOK_TASK).groups["spec"]}
    assert not any(BOOK_CHANGE in path for path in paths)


def test_second_project_needs_no_code_change(db: SeekDB) -> None:
    """Same module, different repository, different language in the task."""

    ingest(INVENTORY, db)
    task = "预约数量不能超过可用量，超量时拒绝并保持已预约数量不变"
    plan = build_plan(db, task)

    assert plan.groups["spec"], "the stock spec must be retrieved by a Chinese task"
    assert any(item.doc.path == "stock/service.py" for item in plan.groups["code"])
    assert plan.candidate_tests
    assert all(selector.startswith("stock/tests/test_reservation.py::") for selector in plan.candidate_tests)
    assert "stock/service.py" in plan.target_files


def test_second_project_prefers_the_reservation_spec(db: SeekDB) -> None:
    ingest(INVENTORY, db)
    plan = build_plan(db, "拒绝超过可用量的预约")
    spec_paths = [item.doc.path for item in plan.groups["spec"]]
    assert spec_paths
    assert any("enforce-reservation-limit" in path for path in spec_paths), spec_paths


def test_english_task_over_the_second_project(db: SeekDB) -> None:
    ingest(INVENTORY, db)
    plan = build_plan(db, "reject reservations above free quantity")
    assert plan.target_files == ["stock/service.py"]
    assert plan.candidate_tests[0].endswith("test_reserve_rejects_quantity_above_free_stock")


def test_context_payload_marks_acceptance_and_regression(db: SeekDB) -> None:
    ingest(ROOT, db)
    plan = build_plan(db, BOOK_TASK)
    plan.assign_tests(
        failed=["demo_app/tests/test_books.py::test_price_filter_is_inclusive_and_keeps_order"],
        passed=[
            "demo_app/tests/test_books.py::test_query_filter_still_works",
            "demo_app/tests/test_books.py::test_category_filter_still_works",
        ],
    )
    payload = context_from_plan(plan, BOOK_TASK, None, None)
    assert payload["target_tests"] == [
        "demo_app/tests/test_books.py::test_price_filter_is_inclusive_and_keeps_order"
    ]
    assert "demo_app/tests/test_books.py::test_query_filter_still_works" in payload["regression_tests"]
    assert payload["probe"]["failed"] == []


def test_context_includes_provenance_fields(tmp_path: Path, db: SeekDB) -> None:
    from minimax_context.config import Settings

    ingest(ROOT, db)
    settings = Settings.load(ROOT)
    payload = build_context(settings, db, BOOK_TASK)
    assert payload["schema"] == "minimax.sdd-context.v2"
    assert payload["terms"]
    for item in payload["items"]:
        assert item["path"] and item["version"] and item["content_hash"]
        assert item["line_start"] >= 1
        assert isinstance(item["score"], float)
    assert set(payload["required"]) == {"spec", "code", "test"}


def test_task_terms_drop_function_words() -> None:
    assert "the" not in task_terms(BOOK_TASK)
    assert "min_price" in task_terms(BOOK_TASK)


def test_collected_docs_skip_pycache_and_framework() -> None:
    docs = collect_docs(ROOT)
    paths = {doc.path for doc in docs}
    assert not any("__pycache__" in path for path in paths)
    assert not any(path.startswith("src/minimax_context") for path in paths)
