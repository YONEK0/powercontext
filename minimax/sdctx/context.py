"""把检索结果组装成一份"可追溯"的开发上下文。

输出必须能定位四类信息:

1. 本次任务适用的**规格条目**;
2. 需要修改的**代码文件或符号**;
3. 用于验证功能与兼容性的**测试**;
4. **已完成事项、待办事项和已有验证结果**（来自 PowerContext 的任务状态）。

每一条都带真实来源（相对路径、行号、sha256），并做一次存在性校验。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .powercontext_client import PowerContextClient
from .retrieve import retrieve
from .seekdb_store import SeekDBStore
from .state import load_state, make_task_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_sources(project_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """校验每条来源在磁盘上真实存在。"""

    for entry in entries:
        rel = str(entry.get("path") or entry.get("spec_path") or "")
        target = project_root / rel
        exists = target.exists()
        entry["source_exists"] = exists
        if exists:
            current = _file_sha256(target)
            entry["source_sha256_now"] = current
            entry["source_current"] = current == entry.get("sha256")
    return entries


def _expand_editable_symbols(
    store: SeekDBStore, code: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """把命中类展开成"可逐个替换的方法"，并剔除只是前缀的粗粒度符号。"""

    collected: dict[str, dict[str, Any]] = {}
    for row in code:
        collected[str(row.get("qualified_name"))] = dict(row)

    for row in list(code):
        if str(row.get("kind")) != "class":
            continue
        class_name = str(row.get("qualified_name"))
        path = str(row.get("path"))
        members = store.fetch_all(
            "SELECT id, path, symbol, qualified_name, kind, start_line, end_line, content, sha256 "
            "FROM ctx_code_symbol WHERE path = %s AND qualified_name LIKE %s "
            "ORDER BY start_line LIMIT %s",
            (path, f"{class_name}.%", limit * 2),
        )
        for member in members:
            member["score"] = float(row.get("score", 0.0))
            collected.setdefault(str(member["qualified_name"]), member)

    # 丢弃"只是别人的前缀"的粗粒度符号，例如 Scheduler（已有 Scheduler.xxx）
    names = list(collected)
    for name in names:
        if any(other != name and other.startswith(f"{name}.") for other in names):
            collected.pop(name, None)

    ordered = sorted(
        collected.values(),
        key=lambda item: (-float(item.get("score", 0.0)), str(item.get("path")), int(item.get("start_line", 0))),
    )
    return ordered[:limit]


def build_context(
    store: SeekDBStore,
    client: PowerContextClient,
    config: Config,
    task: str,
    task_id: str | None = None,
    with_state: bool = True,
) -> dict[str, Any]:
    """检索 + 组装 + 校验，返回开发上下文。"""

    resolved_task_id = task_id or make_task_id(task)
    hits = retrieve(
        store,
        task,
        {
            "specs": config.top_k_specs,
            "code": config.top_k_code,
            "tests": config.top_k_tests,
        },
    )
    terms = hits.pop("terms", [])

    specs = [dict(row) for row in hits["specs"]]
    code = [dict(row) for row in hits["code"]]
    tests = [dict(row) for row in hits["tests"]]

    # 可编辑符号：命中类时展开出它的全部方法，避免模型整类替换
    editable = _expand_editable_symbols(store, code, config.max_code_candidates)

    _verify_sources(config.project_root, specs)
    _verify_sources(config.project_root, code)
    _verify_sources(config.project_root, tests)

    state_block: dict[str, Any] | None = None
    if with_state:
        try:
            loaded = load_state(client, resolved_task_id)
            state_block = loaded.get("state")
            if state_block is None:
                state_block = None
        except Exception as error:  # PowerContext 不可用时不影响上下文生成
            state_block = {"error": f"读取任务状态失败: {error}"}

    progress = (state_block or {}).get("progress") or {}
    context: dict[str, Any] = {
        "schema": "sdctx.context.v1",
        "task": task,
        "task_id": resolved_task_id,
        "generated_at": _now(),
        "database": {
            "engine": store.server_version(),
            "mode": config.seekdb_mode,
            "database": config.seekdb_database,
            "terms": terms,
            "counts": store.row_counts(),
        },
        "specs": specs,
        "code": code,
        "editable_symbols": editable,
        "tests": tests,
        "progress": {
            "done": progress.get("done", []),
            "todo": progress.get("todo", []),
            "verification": (state_block or {}).get("verification"),
            "status": progress.get("status", "unknown"),
        },
        "state_found": state_block is not None,
    }
    context["summary"] = {
        "spec_count": len(specs),
        "code_count": len(code),
        "test_count": len(tests),
        "required_sources": {
            "specs": [
                f"{item.get('spec_path')}#{item.get('item_id')}"
                for item in specs
                if item.get("spec_path")
            ],
            "code": [
                f"{item.get('path')}:{item.get('qualified_name')}"
                for item in code
                if item.get("path")
            ],
            "tests": [
                f"{item.get('path')}::{item.get('test_name')}"
                for item in tests
                if item.get("path")
            ],
        },
    }
    return context


def _fence(text: str, language: str = "") -> str:
    return f"```{language}\n{text}\n```"


def render_markdown(context: dict[str, Any], max_chars: int = 1200) -> str:
    """把上下文渲染成给模型看的 Markdown。"""

    lines: list[str] = []
    lines.append(f"# 开发上下文：{context.get('task')}")
    lines.append("")
    lines.append(f"- task_id: `{context.get('task_id')}`")
    lines.append(f"- 生成时间: {context.get('generated_at')}")
    database = context.get("database") or {}
    lines.append(
        f"- 检索库: {database.get('engine')}（{database.get('mode')} / {database.get('database')}）"
    )
    lines.append("")

    progress = context.get("progress") or {}
    lines.append("## 任务状态（来自 PowerContext）")
    lines.append("")
    lines.append(f"- 状态: {progress.get('status')}")
    lines.append(f"- 已完成: {progress.get('done') or '（无）'}")
    lines.append(f"- 待办: {progress.get('todo') or '（无）'}")
    lines.append(f"- 已有验证结果: {progress.get('verification') or '（无）'}")
    lines.append("")

    lines.append("## 适用规格条目")
    lines.append("")
    for item in context.get("specs") or []:
        lines.append(
            f"### {item.get('item_id')} {item.get('title')} "
            f"（来源: `{item.get('spec_path')}`，版本: {item.get('version') or 'n/a'}）"
        )
        lines.append("")
        lines.append(str(item.get("content", ""))[:max_chars].strip())
        lines.append("")

    lines.append("## 需要修改的代码")
    lines.append("")
    for item in context.get("code") or []:
        lines.append(
            f"### `{item.get('path')}` :: {item.get('qualified_name')} "
            f"（{item.get('kind')}, 行 {item.get('start_line')}-{item.get('end_line')}）"
        )
        lines.append("")
        lines.append(_fence(str(item.get("content", ""))[:max_chars], "python"))
        lines.append("")

    editable = context.get("editable_symbols") or []
    if editable:
        lines.append("## 可编辑符号（完整定义，用于精确替换）")
        lines.append("")
        for item in editable:
            lines.append(
                f"### `{item.get('path')}` :: `{item.get('qualified_name')}` "
                f"（行 {item.get('start_line')}-{item.get('end_line')}）"
            )
            lines.append("")
            lines.append(_fence(str(item.get("content", ""))[:max_chars], "python"))
            lines.append("")

    lines.append("## 用于验证的测试")
    lines.append("")
    for item in context.get("tests") or []:
        lines.append(
            f"### `{item.get('path')}` :: {item.get('test_name')} "
            f"（行 {item.get('start_line')}-{item.get('end_line')}）"
        )
        lines.append("")
        lines.append(_fence(str(item.get("content", ""))[:max_chars], "python"))
        lines.append("")

    return "\n".join(lines)
