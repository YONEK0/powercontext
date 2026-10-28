"""Ingest specs, code and tests into the seekdb document store.

Project directories are discovered rather than named, so pointing the module at
a different repository does not require editing this file.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Iterable

from .db import Doc, SeekDB

SPEC_DIRS = ("openspec/specs", "openspec/changes")
# Framework/runtime directories that are never part of the business project.
# `src/` is deliberately not excluded because many projects keep code there;
# only this module's own package is skipped.
NON_PROJECT_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "data",
    "evidence",
    "fixtures",
    "node_modules",
    "openspec",
    "scripts",
    "tests",
    "dist",
    "build",
}
FRAMEWORK_PACKAGE = Path(__file__).resolve().parent.name


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _lines(text: str, start: int, end: int) -> str:
    return "\n".join(text.splitlines()[start - 1 : end])


def _is_test_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    name = path.name
    if not name.startswith("test_") and not name.endswith("_test.py"):
        return False
    return "/tests/" in f"/{rel}" or name.startswith("test_")


def project_roots(root: Path, configured: Iterable[str] | None = None) -> list[Path]:
    """Return directories that hold the business project's code."""

    if configured:
        found = [root / name for name in configured if (root / name).is_dir()]
        if found:
            return found
    roots: list[Path] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir() or candidate.name in NON_PROJECT_DIRS:
            continue
        if candidate.name.startswith(".") or candidate.name == FRAMEWORK_PACKAGE:
            continue
        if any(candidate.rglob("*.py")):
            roots.append(candidate)
    return roots


def _is_framework_path(path: Path) -> bool:
    return FRAMEWORK_PACKAGE in path.parts


def _code_docs(root: Path, roots: Iterable[Path]) -> list[Doc]:
    docs: list[Doc] = []
    for project in roots:
        for path in sorted(project.rglob("*.py")):
            if "__pycache__" in path.parts or _is_framework_path(path):
                continue
            if _is_test_path(path, root):
                continue
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            line_count = max(1, len(text.splitlines()))
            docs.append(
                Doc(
                    doc_id=f"code:{rel}",
                    kind="code",
                    path=rel,
                    symbol=None,
                    content=text,
                    version=_digest(text)[:12],
                    content_hash=_digest(text),
                    line_start=1,
                    line_end=line_count,
                    metadata={"language": "python", "node": "file"},
                )
            )
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                # Nested definitions are covered by their parent's body.
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.col_offset > 0:
                    continue
                start = int(getattr(node, "lineno", 1))
                end = int(getattr(node, "end_lineno", start))
                body = _lines(text, start, end)
                docs.append(
                    Doc(
                        doc_id=f"code:{rel}#{node.name}",
                        kind="code",
                        path=rel,
                        symbol=node.name,
                        content=body,
                        version=_digest(body)[:12],
                        content_hash=_digest(body),
                        line_start=start,
                        line_end=end,
                        metadata={"language": "python", "node": type(node).__name__},
                    )
                )
    return docs


def _test_docs(root: Path, roots: Iterable[Path]) -> list[Doc]:
    docs: list[Doc] = []
    for project in roots:
        for path in sorted(project.rglob("*.py")):
            if "__pycache__" in path.parts or _is_framework_path(path):
                continue
            if not _is_test_path(path, root):
                continue
            rel = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            line_count = max(1, len(text.splitlines()))
            docs.append(
                Doc(
                    doc_id=f"test:{rel}",
                    kind="test",
                    path=rel,
                    symbol=None,
                    content=text,
                    version=_digest(text)[:12],
                    content_hash=_digest(text),
                    line_start=1,
                    line_end=line_count,
                    metadata={"language": "python", "node": "file"},
                )
            )
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith("test"):
                    continue
                start = int(getattr(node, "lineno", 1))
                end = int(getattr(node, "end_lineno", start))
                body = _lines(text, start, end)
                docs.append(
                    Doc(
                        doc_id=f"test:{rel}#{node.name}",
                        kind="test",
                        path=rel,
                        symbol=node.name,
                        content=body,
                        version=_digest(body)[:12],
                        content_hash=_digest(body),
                        line_start=start,
                        line_end=end,
                        metadata={"language": "python", "node": "test"},
                    )
                )
    return docs


def spec_role(rel: str) -> str:
    """Classify a spec document by OpenSpec convention.

    ``openspec/changes/<change>/specs/<cap>/spec.md`` is the normative delta for
    the change being implemented, ``openspec/specs/<cap>/spec.md`` is the
    canonical requirement set, and ``proposal.md`` / ``design.md`` / ``tasks.md``
    are process documents that restate rather than define requirements. Ranking
    needs this because a long design document repeats more task words than the
    short delta that actually states the rule.
    """

    parts = rel.split("/")
    if "changes" in parts and "specs" in parts and parts[-1] == "spec.md":
        return "delta"
    if parts[:2] == ["openspec", "specs"] and parts[-1] == "spec.md":
        return "canonical"
    return "process"


def _spec_docs(root: Path) -> list[Doc]:
    docs: list[Doc] = []
    paths = sorted(
        {
            path
            for rel in SPEC_DIRS
            if (root / rel).is_dir()
            for path in (root / rel).rglob("*.md")
        }
    )
    for path in paths:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        line_count = max(1, len(text.splitlines()))
        docs.append(
            Doc(
                doc_id=f"spec:{rel}",
                kind="spec",
                path=rel,
                symbol=None,
                content=text,
                version=_digest(text)[:12],
                content_hash=_digest(text),
                line_start=1,
                line_end=line_count,
                metadata={
                    "format": "openspec",
                    "change": "openspec/changes/" in rel,
                    "role": spec_role(rel),
                    "file": path.name,
                },
            )
        )
    return docs


def collect_docs(root: Path, projects: Iterable[str] | None = None) -> list[Doc]:
    roots = project_roots(root, projects)
    return [*_spec_docs(root), *_code_docs(root, roots), *_test_docs(root, roots)]


def ingest(
    root: Path, db: SeekDB, projects: Iterable[str] | None = None
) -> dict[str, int]:
    db.create_schema()
    docs = collect_docs(root, projects)
    db.replace_docs(docs)
    counts: dict[str, int] = {}
    for doc in docs:
        counts[doc.kind] = counts.get(doc.kind, 0) + 1
    return counts
