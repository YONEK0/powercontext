"""用 MiniMax 完成一次真实的代码修改。

协议:模型输出 **符号替换块**，模块用 ``ast`` 精确定位并整段替换。

格式::

    <<<REPLACE schedule/__init__.py Scheduler._run_job>>>
    def _run_job(self, job: "Job") -> None:
        ...
    <<<END>>>

``REPLACE`` 替换一个已有符号（函数 / 类方法 / 类）的完整定义；
``INSERT`` 在某个符号之后插入新代码。

之所以不用"让模型自己写行号 / diff"，是因为模型给出的上下文行和行号
经常与真实文件不一致；按符号定位可以保证改动一定落在真实代码上。

如果模型仍然输出了 unified diff，则回退到 diff 应用器。
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .context import render_markdown
from .minimax_client import MiniMaxClient
from .patching import apply_unified_diff, extract_diff_block
from .symbols import compiles, insert_after_symbol, replace_symbol

BLOCK_RE = re.compile(
    r"<<<(REPLACE|INSERT)\s+(?P<path>[^\s>]+)\s+(?P<symbol>[\w.]+)>>>\s*\n(?P<body>.*?)\n?<<<END>>>",
    re.DOTALL,
)

SYSTEM_PROMPT = (
    "你是一名严谨的软件工程师。你会收到一份开发上下文，包含本次任务适用的规格条目、"
    "相关代码符号与用于验证的测试。请严格按规格实现缺失的行为，同时严格保持既有行为不变。\n"
    "输出格式（必须遵守）：只输出若干个符号替换块，不要输出任何解释文字。\n"
    "每块形如：\n"
    "<<<REPLACE 相对路径 符号名>>>\n"
    "符号的完整新定义（保持原有缩进层级）\n"
    "<<<END>>>\n"
    "相对路径相对于业务工程根目录；符号名使用上下文中给出的 qualified_name。"
    "只能修改上下文中出现过的符号，不要新增文件，不要新增测试。"
)

USER_TEMPLATE = """{context}

---

# 本次任务

{task}

# 可替换的符号

{symbols}

# 输出要求

1. 只输出符号替换块，格式见系统提示；
2. 每个块给出该符号的**完整新定义**（包含所有行，不要省略）；
3. 必须实现规格中的全部要求，不得删除或弱化既有断言；
4. 只修改与规格直接相关的符号，其他符号保持原样；
5. 不要输出解释、不要输出 markdown 标题、不要输出 diff。
{feedback}"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_prompt(context: dict[str, Any], task: str, feedback: str = "") -> str:
    symbols = []
    for row in context.get("editable_symbols") or []:
        symbols.append(
            f"- `{row.get('path')}` :: `{row.get('qualified_name')}` "
            f"（{row.get('kind')}, 行 {row.get('start_line')}-{row.get('end_line')}）"
        )
    return USER_TEMPLATE.format(
        context=render_markdown(context),
        task=task,
        symbols="\n".join(symbols) or "（无）",
        feedback=feedback,
    )


def parse_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for match in BLOCK_RE.finditer(text or ""):
        body = match.group("body")
        # 去掉可能被模型整体缩进的公共前缀
        lines = body.splitlines()
        stripped = [line for line in lines if line.strip()]
        indent = min((len(line) - len(line.lstrip()) for line in stripped), default=0)
        if indent:
            lines = [line[indent:] if line.strip() else "" for line in lines]
        blocks.append(
            {
                "action": match.group(1),
                "path": match.group("path"),
                "symbol": match.group("symbol"),
                "body": "\n".join(lines),
            }
        )
    return blocks


def _snapshot(root: Path, paths: list[str]) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for rel in paths:
        target = root / rel
        if target.exists():
            snapshot[rel] = target.read_text(encoding="utf-8")
    return snapshot


def apply_blocks(project_root: Path, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """把符号替换块落到真实文件上，并做语法校验。"""

    applied: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    for block in blocks:
        target = project_root / block["path"]
        if not target.exists():
            missing.append(f"{block['path']}::{block['symbol']} (文件不存在)")
            continue
        ok = (
            replace_symbol(target, block["symbol"], block["body"])
            if block["action"] == "REPLACE"
            else insert_after_symbol(target, block["symbol"], block["body"])
        )
        if not ok:
            missing.append(f"{block['path']}::{block['symbol']} (符号未找到)")
            continue
        valid, message = compiles(target)
        if not valid:
            invalid.append(f"{block['path']}::{block['symbol']} {message}")
            break
        applied.append(f"{block['path']}::{block['symbol']}")
    return {"applied": applied, "missing": missing, "invalid": invalid}


def _revert(root: Path, snapshot: dict[str, str]) -> None:
    for rel, content in snapshot.items():
        (root / rel).write_text(content, encoding="utf-8")


def run_tests(config: Config, timeout: float = 300.0) -> dict[str, Any]:
    """在业务工程目录里跑一遍测试，返回退出码与输出。"""

    if not config.test_command:
        return {"ok": True, "exit_code": 0, "output": "(未配置测试命令，跳过)"}
    try:
        completed = subprocess.run(
            list(config.test_command),
            cwd=str(config.project_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": -1, "output": "测试超时"}
    output = (completed.stdout or "") + (completed.stderr or "")
    return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "output": output}


def generate_change(
    client: MiniMaxClient,
    config: Config,
    context: dict[str, Any],
    task: str,
    apply: bool = True,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """调用 MiniMax 生成并（可选）落地一次代码修改。"""

    candidate_paths = sorted(
        {
            str(row.get("path"))
            for row in (context.get("editable_symbols") or []) + (context.get("code") or [])
            if row.get("path")
        }
    )
    attempts: list[dict[str, Any]] = []
    feedback = ""
    final: dict[str, Any] | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = build_prompt(context, task, feedback)
        turn = client.chat(
            messages=[{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        blocks = parse_blocks(turn.response_text)
        diff_text = "" if blocks else extract_diff_block(turn.response_text)
        record = {
            "attempt": attempt,
            "prompt": prompt,
            "response_text": turn.response_text,
            "blocks": blocks,
            "diff": diff_text,
            "usage": turn.usage,
            "elapsed_seconds": round(turn.elapsed_seconds, 3),
        }
        attempts.append(record)

        if not blocks and not diff_text:
            feedback = "\n\n# 上一次输出无法解析\n请严格按照 <<<REPLACE 路径 符号名>>> ... <<<END>>> 的格式重新输出。"
            continue

        if not apply:
            final = {**record, "applied": False, "error": "仅生成，未落盘（apply=False）"}
            break

        before = _snapshot(config.project_root, candidate_paths)
        if blocks:
            outcome = apply_blocks(config.project_root, blocks)
            if outcome["missing"] or outcome["invalid"]:
                _revert(config.project_root, before)
                feedback = (
                    "\n\n# 上一次输出无法落地\n"
                    + "\n".join(f"- {item}" for item in outcome["missing"] + outcome["invalid"])
                    + "\n请修正后重新输出，符号名必须与上下文完全一致，替换内容必须是可解析的 Python。"
                )
                record["outcome"] = outcome
                continue
        else:
            patch_result = apply_unified_diff(config.project_root, diff_text)
            if not patch_result.applied and not patch_result.created:
                feedback = (
                    "\n\n# 上一次 diff 无法应用\n请重新输出符号替换块，"
                    "符号名必须与上下文完全一致。"
                )
                continue

        # 落盘后立刻验证：测试不通过就回滚并把失败信息喂回模型
        test_result = run_tests(config)
        record["test_result"] = {
            "exit_code": test_result["exit_code"],
            "output": test_result["output"][-4000:],
        }
        if not test_result["ok"]:
            _revert(config.project_root, before)
            feedback = (
                "\n\n# 上一次修改导致测试失败（已回滚）\n"
                f"测试命令: {' '.join(config.test_command)}\n"
                f"退出码: {test_result['exit_code']}\n"
                "```\n" + test_result["output"][-3000:] + "\n```\n"
                "请修正你的修改：只实现规格要求的行为，并保证既有测试全部通过。"
            )
            continue

        after = _snapshot(config.project_root, candidate_paths)
        changed: list[str] = []
        diffs: list[str] = []
        for rel in sorted(set(before) | set(after)):
            old = before.get(rel, "")
            new = after.get(rel, "")
            if old == new:
                continue
            changed.append(rel)
            diffs.append(
                "\n".join(
                    difflib.unified_diff(
                        old.splitlines(),
                        new.splitlines(),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                        lineterm="",
                        n=3,
                    )
                )
            )
        final = {
            **record,
            "applied": bool(changed),
            "changed_files": changed,
            "resulting_diff": "\n".join(diffs),
        }
        break

    if final is None:
        final = {
            "applied": False,
            "error": "模型多次输出都无法落地",
            "attempts": attempts,
        }
    else:
        final["attempts"] = attempts
        final["schema"] = "sdctx.minimax-turn.v1"
        final["generated_at"] = _now()
        final["task"] = task
        final["task_id"] = context.get("task_id")
        final["model"] = config.minimax_model
    return final


def write_evidence(config: Config, name: str, payload: dict[str, Any] | str) -> Path:
    """把证据写入 ``evidence/`` 目录。"""

    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    target = config.evidence_dir / name
    if name.endswith(".json"):
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        target.write_text(
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return target
