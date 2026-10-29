"""按"符号"定位并替换代码。

模型直接给出行号或 diff 上下文都容易出错，因此本模块采用更稳的协议：

* 模型给出 **完整的符号替换块**（函数 / 类方法的原文）;
* 模块用 ``ast`` 在真实文件里定位该符号的行范围，再整段替换。

这样"修改"一定落在真实的代码位置上，也不会误伤文件里的其他内容。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SymbolRange:
    qualified_name: str
    path: str
    start_line: int  # 1-based, 包含
    end_line: int  # 1-based, 包含


def find_symbol(path: Path, qualified_name: str) -> SymbolRange | None:
    """在文件里找到 ``qualified_name``（如 ``Scheduler._run_job``）的定义范围。"""

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return None

    parts = qualified_name.split(".")
    node: ast.AST | None = tree
    for part in parts:
        found: ast.AST | None = None
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == part:
                found = child
                break
        if found is None:
            return None
        node = found

    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return None
    start = node.lineno
    if isinstance(node, ast.ClassDef):
        # 类替换需要带上装饰器行
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    end = getattr(node, "end_lineno", start) or start
    return SymbolRange(qualified_name=qualified_name, path=str(path), start_line=start, end_line=end)


def list_symbols(path: Path) -> list[str]:
    """列出文件里所有可替换的符号名。"""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    names: list[str] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = f"{prefix}{child.name}"
                names.append(qualified)
                walk(child, f"{qualified}.")

    walk(tree, "")
    return names


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(lines: list[str], prefix: str) -> list[str]:
    """把替换内容对齐到原有的缩进层级。

    模型给出的替换块可能整体少一级缩进（方法体按顶层书写），
    这里按"相对缩进"整体平移，保证块内部的相对结构不变。
    """

    if not lines:
        return []
    first = _indent_of(lines[0])
    delta = len(prefix) - len(first)
    adjusted: list[str] = []
    for line in lines:
        if not line.strip():
            adjusted.append("")
            continue
        indent = _indent_of(line)
        new_indent = max(0, len(indent) + delta)
        adjusted.append(" " * new_indent + line.strip())
    return adjusted


def replace_symbol(path: Path, qualified_name: str, new_text: str) -> bool:
    """把文件中某个符号的整体替换为 ``new_text``，成功返回 True。

    替换内容会按原符号的缩进层级重新对齐，因此模型即使把方法体写成
    顶格缩进，落到文件里也仍然是合法的类成员定义。
    """

    target = find_symbol(path, qualified_name)
    if target is None:
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = _indent_of(lines[target.start_line - 1])
    replacement = _reindent(new_text.rstrip("\n").splitlines(), prefix)
    updated = lines[: target.start_line - 1] + replacement + lines[target.end_line :]
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return True


def insert_after_symbol(path: Path, qualified_name: str, new_text: str) -> bool:
    """在某个符号之后插入新代码。"""

    target = find_symbol(path, qualified_name)
    if target is None:
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    prefix = _indent_of(lines[target.start_line - 1])
    addition = _reindent(new_text.rstrip("\n").splitlines(), prefix)
    updated = lines[: target.end_line] + addition + lines[target.end_line :]
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    return True


def compiles(path: Path) -> tuple[bool, str]:
    """检查文件是否可以被 Python 解析。"""

    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as error:
        return False, f"SyntaxError: {error}"
    return True, ""
