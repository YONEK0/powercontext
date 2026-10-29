"""上下文模块自身的单元测试（不需要数据库与网络）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdctx.agent import parse_blocks  # noqa: E402
from sdctx.ingest import parse_python_symbols, parse_spec_markdown, parse_test_cases  # noqa: E402
from sdctx.retrieve import extract_terms  # noqa: E402
from sdctx.state import MAX_MEMORY_CHARS, _summarize, make_task_id  # noqa: E402
from sdctx.symbols import compiles, find_symbol, replace_symbol  # noqa: E402


# --------------------------------------------------------------------- 检索词项
def test_extract_terms_splits_identifiers_and_cjk():
    terms = extract_terms("让 Scheduler.run_pending 调用 on_error，隔离任务异常")
    assert "scheduler" in terms
    assert "run_pending" in terms
    assert "on_error" in terms
    assert "隔离" in terms
    assert "任务" in terms


def test_extract_terms_is_deterministic():
    text = "run_pending 异常隔离"
    assert extract_terms(text) == extract_terms(text)


# --------------------------------------------------------------------- 规格解析
def test_parse_spec_markdown_extracts_items_and_meta():
    text = """# SPEC-003 任务执行异常隔离

- 状态: active
- 类型: requirement
- 版本: v2

## R-003-001 单任务失败不得中断同轮其他任务

必须隔离异常。

## R-003-002 失败必须可观测

异常要能被观测到。
"""
    title, meta, items = parse_spec_markdown(text, "specs/SPEC-003.md")
    assert title == "SPEC-003 任务执行异常隔离"
    assert meta["版本"] == "v2"
    assert meta["类型"] == "requirement"
    assert [item.item_id for item in items] == ["R-003-001", "R-003-002"]
    assert items[0].version == "v2"
    assert "必须隔离异常" in items[0].content


def test_parse_spec_markdown_handles_missing_meta():
    _, meta, items = parse_spec_markdown("# T\n\n## R-1 x\n\nbody\n", "specs/T.md")
    assert meta == {}
    assert items[0].kind == "requirement"


# --------------------------------------------------------------------- 代码解析
def test_parse_python_symbols_records_line_ranges():
    source = "class A:\n    def b(self):\n        return 1\n\n\ndef c():\n    return 2\n"
    symbols = parse_python_symbols(source, "m.py")
    names = {symbol.qualified_name for symbol in symbols}
    assert names == {"A", "A.b", "c"}
    method = next(symbol for symbol in symbols if symbol.qualified_name == "A.b")
    assert method.start_line == 2
    assert method.end_line == 3
    assert "return 1" in method.content


def test_parse_test_cases_only_collects_test_functions():
    source = "def test_ok():\n    assert True\n\n\ndef helper():\n    pass\n"
    cases = parse_test_cases(source, "tests/test_x.py")
    assert [case.test_name for case in cases] == ["test_ok"]


# --------------------------------------------------------------------- 符号定位
def test_find_symbol_and_replace(tmp_path: Path):
    target = tmp_path / "m.py"
    target.write_text(
        "class S:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "\n"
        "    def run(self):\n"
        "        return self.x\n",
        encoding="utf-8",
    )
    found = find_symbol(target, "S.run")
    assert found is not None and found.start_line == 5

    # 模型常把方法体按顶层缩进给出，这里必须被自动对齐
    assert replace_symbol(
        target,
        "S.run",
        "def run(self):\n    try:\n        return self.x\n    except Exception:\n        return None",
    )
    assert compiles(target)[0]
    assert "    def run(self):" in target.read_text(encoding="utf-8")
    assert "        return None" in target.read_text(encoding="utf-8")


def test_replace_symbol_reports_missing_symbol(tmp_path: Path):
    target = tmp_path / "m.py"
    target.write_text("x = 1\n", encoding="utf-8")
    assert replace_symbol(target, "Nope.method", "def method(self):\n    pass") is False


# --------------------------------------------------------------------- 模型协议
def test_parse_blocks_extracts_symbol_replacements():
    text = """<<<REPLACE schedule/__init__.py Scheduler.__init__>>>
    def __init__(self) -> None:
        self.jobs = []
<<<END>>>

<<<INSERT schedule/__init__.py Scheduler.run>>>
    def extra(self):
        pass
<<<END>>>
"""
    blocks = parse_blocks(text)
    assert [block["action"] for block in blocks] == ["REPLACE", "INSERT"]
    assert blocks[0]["symbol"] == "Scheduler.__init__"
    assert blocks[0]["body"].splitlines()[0] == "def __init__(self) -> None:"
    assert blocks[1]["symbol"] == "Scheduler.run"


def test_parse_blocks_ignores_unrelated_text():
    assert parse_blocks("这里没有任何替换块") == []


# --------------------------------------------------------------------- 任务状态
def test_make_task_id_is_stable_and_readable():
    first = make_task_id("让 Scheduler.run_pending 隔离异常")
    second = make_task_id("让 Scheduler.run_pending 隔离异常")
    assert first == second
    assert first.startswith("scheduler-run-pending")


def test_memory_summary_is_bounded():
    payload = {
        "task_id": "t-1",
        "status": "in_progress",
        "context": {"specs": [1] * 50, "code": [1] * 50, "tests": [1] * 50},
        "progress": {"done": ["x"] * 50, "todo": ["y"] * 50},
        "verification": "z" * 2000,
    }
    summary = _summarize(payload)
    assert len(summary) <= MAX_MEMORY_CHARS
    assert summary.startswith("SDCTX-STATE t-1")
