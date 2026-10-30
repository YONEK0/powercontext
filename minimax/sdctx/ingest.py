"""把工程资料（规格 / 代码 / 测试）导入 seekdb。

规格文件解析规则（Markdown）:

* 一级标题 ``# SPEC-xxx 标题`` 是规格文档本身;
* 标题下方的 ``- 键: 值`` 是规格元数据（类型、状态、版本、目标测试等）;
* 每个二级标题 ``## R-xxx-xxx 标题`` 是一个可被单独检索的规格条目。

代码与测试使用标准库 ``ast`` 解析，抽取真实的符号与行号，
保证检索结果里的"来源"真实存在、可定位。
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .seekdb_store import SeekDBStore

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".sdctx",
    ".venv",
    "venv",
    "node_modules",
    # 修改前的原始快照，只用于生成代码差异，不参与检索
    "upstream_reference",
}
META_RE = re.compile(r"^-\s*([^:：]+)\s*[:：]\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:64]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class SpecItem:
    spec_path: str
    spec_title: str
    item_id: str
    title: str
    kind: str
    version: str
    content: str
    sha256: str


@dataclass
class CodeSymbol:
    path: str
    symbol: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    content: str
    sha256: str


@dataclass
class TestCase:
    path: str
    test_name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    content: str
    sha256: str


@dataclass
class IngestSummary:
    documents: int = 0
    spec_items: int = 0
    code_symbols: int = 0
    test_cases: int = 0
    spec_files: list[str] = field(default_factory=list)
    code_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "spec_items": self.spec_items,
            "code_symbols": self.code_symbols,
            "test_cases": self.test_cases,
            "spec_files": self.spec_files,
            "code_files": self.code_files,
            "test_files": self.test_files,
        }


# --------------------------------------------------------------------- 规格解析
def parse_spec_markdown(text: str, rel_path: str) -> tuple[str, dict[str, str], list[SpecItem]]:
    lines = text.splitlines()
    doc_title = rel_path
    meta: dict[str, str] = {}
    items: list[SpecItem] = []

    current_item_id = ""
    current_title = ""
    current_body: list[str] = []
    in_item = False

    def flush() -> None:
        nonlocal current_item_id, current_title, current_body, in_item
        if in_item and current_item_id:
            body = "\n".join(current_body).strip()
            items.append(
                SpecItem(
                    spec_path=rel_path,
                    spec_title=doc_title,
                    item_id=current_item_id,
                    title=current_title,
                    kind=meta.get("类型") or meta.get("type") or "requirement",
                    version=meta.get("版本") or meta.get("version") or "",
                    content=body,
                    sha256=sha256_text(body),
                )
            )
        current_item_id = ""
        current_title = ""
        current_body = []
        in_item = False

    seen_first_heading = False
    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            if level == 1 and not seen_first_heading:
                doc_title = title
                seen_first_heading = True
                continue
            if level == 2:
                flush()
                in_item = True
                token = title.split()[0] if title else ""
                current_item_id = token if re.match(r"^[A-Za-z]{1,6}[-_0-9A-Za-z]*$", token) else stable_id(rel_path, title)[:16]
                current_title = title
                continue
        if in_item:
            current_body.append(line)
        else:
            meta_match = META_RE.match(line)
            if meta_match:
                meta[meta_match.group(1).strip()] = meta_match.group(2).strip()
    flush()
    return doc_title, meta, items


# --------------------------------------------------------------------- 代码解析
def _node_symbols(tree: ast.AST, path: str, source_lines: list[str]) -> list[CodeSymbol]:
    symbols: list[CodeSymbol] = []
    stack: list[tuple[str, ast.AST]] = [("", tree)]
    while stack:
        prefix, node = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = child.name
                qualified = f"{prefix}{name}"
                kind = (
                    "class"
                    if isinstance(child, ast.ClassDef)
                    else ("async_function" if isinstance(child, ast.AsyncFunctionDef) else "function")
                )
                start = child.lineno
                end = getattr(child, "end_lineno", start) or start
                body = "\n".join(source_lines[start - 1 : end])
                symbols.append(
                    CodeSymbol(
                        path=path,
                        symbol=name,
                        qualified_name=qualified,
                        kind=kind,
                        start_line=start,
                        end_line=end,
                        content=body,
                        sha256=sha256_text(body),
                    )
                )
                stack.append((f"{qualified}.", child))
    symbols.sort(key=lambda item: (item.start_line, item.qualified_name))
    return symbols


def parse_python_symbols(text: str, rel_path: str) -> list[CodeSymbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return _node_symbols(tree, rel_path, text.splitlines())


def parse_test_cases(text: str, rel_path: str) -> list[TestCase]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    source_lines = text.splitlines()
    cases: list[TestCase] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        body = "\n".join(source_lines[start - 1 : end])
        cases.append(
            TestCase(
                path=rel_path,
                test_name=node.name,
                qualified_name=node.name,
                kind="test_function",
                start_line=start,
                end_line=end,
                content=body,
                sha256=sha256_text(body),
            )
        )
    cases.sort(key=lambda item: (item.path, item.start_line))
    return cases


# --------------------------------------------------------------------- 文件遍历
def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


# --------------------------------------------------------------------- 导入入口
def ingest_project(store: SeekDBStore, project_root: Path) -> IngestSummary:
    """把一个业务工程的规格、代码、测试导入 seekdb（幂等）。"""

    project_root = Path(project_root).resolve()
    if not project_root.exists():
        raise FileNotFoundError(f"业务工程目录不存在: {project_root}")

    summary = IngestSummary()
    now = _now()

    # 1) 规格
    spec_dir = project_root / "specs"
    spec_files = _iter_files(spec_dir, (".md",)) if spec_dir.exists() else []
    for path in spec_files:
        rel = str(path.relative_to(project_root))
        text = path.read_text(encoding="utf-8")
        doc_title, meta, items = parse_spec_markdown(text, rel)
        summary.spec_files.append(rel)
        summary.documents += 1
        store.execute(
            "INSERT INTO ctx_document (id, kind, path, title, content, sha256, meta, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE title=VALUES(title), content=VALUES(content), "
            "sha256=VALUES(sha256), meta=VALUES(meta), updated_at=VALUES(updated_at)",
            (
                stable_id("document", rel),
                "spec",
                rel,
                doc_title,
                text,
                sha256_text(text),
                repr(meta),
                now,
            ),
        )
        for item in items:
            summary.spec_items += 1
            store.execute(
                "INSERT INTO ctx_spec_item (id, spec_path, spec_title, item_id, title, kind, version, "
                "content, sha256, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE spec_title=VALUES(spec_title), title=VALUES(title), "
                "kind=VALUES(kind), version=VALUES(version), content=VALUES(content), "
                "sha256=VALUES(sha256), updated_at=VALUES(updated_at)",
                (
                    stable_id("spec_item", rel, item.item_id),
                    item.spec_path,
                    item.spec_title,
                    item.item_id,
                    item.title,
                    item.kind,
                    item.version,
                    item.content,
                    item.sha256,
                    now,
                ),
            )

    # 2) 代码
    code_files = [
        path
        for path in _iter_files(project_root, (".py",))
        if "tests" not in path.relative_to(project_root).parts
        and "specs" not in path.relative_to(project_root).parts
    ]
    for path in code_files:
        rel = str(path.relative_to(project_root))
        text = path.read_text(encoding="utf-8")
        summary.code_files.append(rel)
        summary.documents += 1
        store.execute(
            "INSERT INTO ctx_document (id, kind, path, title, content, sha256, meta, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE title=VALUES(title), content=VALUES(content), "
            "sha256=VALUES(sha256), updated_at=VALUES(updated_at)",
            (stable_id("document", rel), "code", rel, path.name, text, sha256_text(text), "", now),
        )
        for symbol in parse_python_symbols(text, rel):
            summary.code_symbols += 1
            store.execute(
                "INSERT INTO ctx_code_symbol (id, path, symbol, qualified_name, kind, start_line, "
                "end_line, content, sha256, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE qualified_name=VALUES(qualified_name), kind=VALUES(kind), "
                "start_line=VALUES(start_line), end_line=VALUES(end_line), content=VALUES(content), "
                "sha256=VALUES(sha256), updated_at=VALUES(updated_at)",
                (
                    stable_id("code_symbol", rel, symbol.qualified_name),
                    symbol.path,
                    symbol.symbol,
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.start_line,
                    symbol.end_line,
                    symbol.content,
                    symbol.sha256,
                    now,
                ),
            )

    # 3) 测试
    test_dir = project_root / "tests"
    test_files = _iter_files(test_dir, (".py",)) if test_dir.exists() else []
    for path in test_files:
        rel = str(path.relative_to(project_root))
        text = path.read_text(encoding="utf-8")
        summary.test_files.append(rel)
        summary.documents += 1
        store.execute(
            "INSERT INTO ctx_document (id, kind, path, title, content, sha256, meta, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE title=VALUES(title), content=VALUES(content), "
            "sha256=VALUES(sha256), updated_at=VALUES(updated_at)",
            (stable_id("document", rel), "test", rel, path.name, text, sha256_text(text), "", now),
        )
        for case in parse_test_cases(text, rel):
            summary.test_cases += 1
            store.execute(
                "INSERT INTO ctx_test_case (id, path, test_name, qualified_name, kind, start_line, "
                "end_line, content, sha256, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE qualified_name=VALUES(qualified_name), kind=VALUES(kind), "
                "start_line=VALUES(start_line), end_line=VALUES(end_line), content=VALUES(content), "
                "sha256=VALUES(sha256), updated_at=VALUES(updated_at)",
                (
                    stable_id("test_case", rel, case.test_name),
                    case.path,
                    case.test_name,
                    case.qualified_name,
                    case.kind,
                    case.start_line,
                    case.end_line,
                    case.content,
                    case.sha256,
                    now,
                ),
            )

    return summary
