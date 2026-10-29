"""最小 unified-diff 解析与应用（只依赖标准库）。

支持模型常见的输出形式:

* ``diff --git a/x b/x`` / ``--- a/x`` / ``+++ b/x`` 头;
* ``@@ -l,c +l,c @@`` 上下文块;
* 新增文件 (``--- /dev/null``) 与删除文件。

应用策略是"上下文匹配"：先在期望行号附近找，找不到再全局精确匹配，
匹配不到就报错而不是静默写坏文件。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def _strip_prefix(path: str) -> str:
    """去掉 unified diff 里的 ``a/`` / ``b/`` 前缀。"""

    cleaned = (path or "").strip().split("\t")[0]
    if cleaned in {"", "/dev/null"}:
        return cleaned
    if cleaned.startswith(('"')) and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    for prefix in ("a/", "b/"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :]
    return cleaned


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FilePatch:
    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def target(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def is_new_file(self) -> bool:
        return self.old_path == "/dev/null"


@dataclass
class PatchResult:
    applied: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def parse_unified_diff(text: str) -> list[FilePatch]:
    """把 unified diff 文本解析成 ``FilePatch`` 列表。"""

    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\n")

        header = DIFF_HEADER_RE.match(line)
        if header:
            current = FilePatch(
                old_path=_strip_prefix(header.group(1)),
                new_path=_strip_prefix(header.group(2)),
            )
            patches.append(current)
            hunk = None
            continue

        if line.startswith("--- "):
            if current is None:
                current = FilePatch(old_path="", new_path="")
                patches.append(current)
            current.old_path = _strip_prefix(line[4:])
            hunk = None
            continue

        if line.startswith("+++ "):
            if current is None:
                current = FilePatch(old_path="", new_path="")
                patches.append(current)
            current.new_path = _strip_prefix(line[4:])
            hunk = None
            continue

        match = HUNK_RE.match(line)
        if match:
            if current is None:
                current = FilePatch(old_path="", new_path="")
                patches.append(current)
            hunk = Hunk(
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or 1),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or 1),
            )
            current.hunks.append(hunk)
            continue

        if hunk is not None and line[:1] in {" ", "+", "-", "\\"}:
            if line.startswith("\\"):
                continue
            hunk.lines.append(line)
            continue
        # 其他行（index、mode、rename 等）忽略
    return [patch for patch in patches if patch.hunks]


def _segments(lines: list[str]) -> list[tuple[str, list[str]]]:
    """把 hunk 行拆成 (类型, 行) 片段: ctx / del / add。"""

    segments: list[tuple[str, list[str]]] = []
    for line in lines:
        if not line:
            continue
        kind = {" ": "ctx", "-": "del", "+": "add"}.get(line[0])
        if kind is None:
            continue
        if segments and segments[-1][0] == kind:
            segments[-1][1].append(line[1:])
        else:
            segments.append((kind, [line[1:]]))
    return segments


def _find_sequence(haystack: list[str], needle: list[str], hint: int, window: int = 400) -> int:
    """在 haystack 中定位 needle，优先在 hint 附近查找。"""

    if not needle:
        return -1
    span = len(needle)
    begin = max(0, hint - window)
    end = min(len(haystack), hint + window)
    for index in range(begin, end):
        if haystack[index : index + span] == needle:
            return index
    for index in range(0, len(haystack)):
        if haystack[index : index + span] == needle:
            return index
    return -1


def _find_relaxed(haystack: list[str], needle: list[str], hint: int) -> tuple[int, int]:
    """宽松定位：依次尝试 needle 的更长前缀，返回 (位置, 匹配长度)。"""

    for length in range(len(needle), 0, -1):
        index = _find_sequence(haystack, needle[:length], hint)
        if index >= 0:
            return index, length
    return -1, 0


def _apply_hunk(original: list[str], hunk: Hunk) -> tuple[list[str], int]:
    """把一个 hunk 应用到 original 列表上。

    优先按"整段上下文"精确匹配；模型给出的上下文行有时并不相邻，
    这时退化为"按被删除的行定位、按新增的行替换"。
    """

    segments = _segments(hunk.lines)
    if not segments:
        return original, hunk.old_start - 1

    old_block = [line for kind, group in segments if kind in {"ctx", "del"} for line in group]
    new_block = [line for kind, group in segments if kind in {"ctx", "add"} for line in group]

    # 1) 完整上下文精确匹配
    index = _find_sequence(original, old_block, hunk.old_start - 1)
    if index >= 0:
        return original[:index] + new_block + original[index + len(old_block) :], index

    # 2) 退化为"按删除行定位"
    for position, (kind, group) in enumerate(segments):
        if kind != "del":
            continue
        index, matched = _find_relaxed(original, group, hunk.old_start - 1)
        if index < 0:
            continue
        group = group[:matched]
        added: list[str] = []
        for follow_kind, follow_group in segments[position + 1 :]:
            if follow_kind == "add":
                added.extend(follow_group)
            elif follow_kind == "del":
                break
        return original[:index] + added + original[index + len(group) :], index

    # 3) 纯插入：用上下文定位插入点（允许上下文被模型截断）
    for position, (kind, group) in enumerate(segments):
        if kind != "ctx":
            continue
        index, matched = _find_relaxed(original, group, hunk.old_start - 1)
        if index < 0:
            continue
        added: list[str] = []
        for follow_kind, follow_group in segments[position + 1 :]:
            if follow_kind == "add":
                added.extend(follow_group)
            elif follow_kind == "del":
                break
        insert_at = index + matched
        return original[:insert_at] + added + original[insert_at:], insert_at

    raise ValueError(f"无法在文件中定位 hunk @@ -{hunk.old_start} @@")


def apply_unified_diff(root: Path, diff_text: str) -> PatchResult:
    """把 unified diff 应用到 ``root`` 目录下，返回应用结果。"""

    result = PatchResult()
    for patch in parse_unified_diff(diff_text):
        target = patch.target
        if not target or target == "/dev/null":
            continue
        path = (root / target).resolve()
        if not str(path).startswith(str(root.resolve())):
            result.skipped.append(target)
            continue
        if patch.is_new_file:
            content: list[str] = []
            for hunk in patch.hunks:
                content.extend(line[1:] for line in hunk.lines if line.startswith((" ", "+")))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(content) + "\n", encoding="utf-8")
            result.created.append(target)
            continue
        if not path.exists():
            result.skipped.append(target)
            continue
        original = path.read_text(encoding="utf-8").splitlines()
        for hunk in patch.hunks:
            original, _ = _apply_hunk(original, hunk)
        path.write_text("\n".join(original) + "\n", encoding="utf-8")
        result.applied.append(target)
    return result


def unified_diff_of(before: str, after: str, path: str) -> str:
    """生成标准 unified diff 文本（用于证据留档）。"""

    import difflib

    diff = difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
        n=3,
    )
    return "\n".join(diff)


def extract_diff_block(text: str) -> str:
    """从模型输出里取出 ``diff`` 代码块。"""

    blocks = re.findall(r"```(?:diff|patch)?\s*\n(.*?)```", text or "", re.DOTALL)
    for block in blocks:
        if "@@" in block and ("---" in block or "diff --git" in block):
            return block.strip()
    if "@@" in (text or "") and "---" in (text or ""):
        start = text.index("---")
        return text[start:].strip()
    return ""
