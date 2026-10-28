"""MiniMax OpenAI-compatible patch generation and replay."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from .config import Settings
from .context import render_for_agent

SYSTEM_PROMPT = """You are a senior software engineer working on a spec-driven task.
Return only a unified git diff that implements the task and preserves the
existing behavior named in the spec. Do not include prose, markdown fences,
or changes unrelated to the task. The patch must apply with `git apply`."""


class MiniMaxClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def chat(self, messages: list[dict[str, str]], *, purpose: str) -> str:
        if not self.settings.minimax_api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set; use agent --replay for the recorded patch")
        response = httpx.post(
            f"{self.settings.minimax_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.settings.minimax_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.minimax_model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": 4096,
            },
            timeout=self.settings.minimax_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        self._record(purpose, messages, payload, content)
        return content

    def _record(self, purpose: str, messages: list[dict[str, str]], response: dict, content: str) -> None:
        target = self.settings.evidence_dir / "minimax"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{int(time.time())}-{purpose}.json"
        path.write_text(
            json.dumps(
                {
                    "model": self.settings.minimax_model,
                    "request": {"messages": messages},
                    "response": {"content": content, "usage": response.get("usage", {})},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def build_patch_messages(task: str, context: dict) -> list[dict[str, str]]:
    files = context.get("target_files") or []
    targets = context.get("target_tests") or []
    regression = context.get("regression_tests") or []
    user = f"""{render_for_agent(context)}

IMPLEMENTATION TASK:
{task}
"""
    if files:
        user += "\nFiles in scope:\n" + "\n".join(f"- {path}" for path in files) + "\n"
    if targets:
        user += "\nAcceptance test:\n" + "\n".join(f"- {sel}" for sel in targets) + "\n"
    if regression:
        user += (
            "\nThese existing tests must keep passing:\n"
            + "\n".join(f"- {sel}" for sel in regression)
            + "\n"
        )
    user += "\nReturn one unified diff only."
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def extract_patch(content: str) -> str:
    text = content.strip()
    text = re.sub(r"^```(?:diff|patch)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("diff --git")
    if start < 0:
        start = text.find("--- ")
    if start < 0:
        raise ValueError("MiniMax response did not contain a unified diff")
    patch = text[start:].strip()
    if not patch:
        raise ValueError("MiniMax returned an empty patch")
    return patch + "\n"


def repair_hunk_counts(patch: str) -> str:
    """Recompute unified-diff hunk counts from the actual body lines.

    Models often produce a correct diff body with a stale ``@@ -a,b +c,d @@``
    header, and the ``index <sha>..000000000000`` line names a blob that does
    not exist. Recomputing counts and dropping that metadata is deterministic
    and leaves the model's actual edit unchanged.
    """

    lines = patch.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    while index < len(lines):
        line = lines[index]
        match = header_re.match(line.rstrip("\n"))
        if not match:
            if not line.startswith("index "):
                out.append(line)
            index += 1
            continue
        old_start, new_start = int(match.group(1)), int(match.group(3))
        tail = match.group(5)
        body_start = index + 1
        cursor = body_start
        old_count = new_count = 0
        while cursor < len(lines):
            body = lines[cursor]
            if body.startswith("@@"):
                break
            if body.startswith("\\ No newline"):
                cursor += 1
                continue
            if body.startswith(" "):
                old_count += 1
                new_count += 1
            elif body.startswith("-"):
                old_count += 1
            elif body.startswith("+"):
                new_count += 1
            else:
                break
            cursor += 1
        out.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@{tail}\n")
        out.extend(lines[body_start:cursor])
        index = cursor
    return "".join(out)


def _patch_paths(patch: str) -> list[str]:
    """Return the post-image paths a unified diff claims to modify."""

    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].strip()
        if target == "/dev/null":
            continue
        for prefix in ("b/", "a/"):
            if target.startswith(prefix):
                target = target[len(prefix) :]
                break
        if target and target not in paths:
            paths.append(target)
    return paths


def _fingerprint(root: Path, paths: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in paths:
        path = root / rel
        result[rel] = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        )
    return result


def _git_prefix(root: Path) -> str:
    """Return the path from the git repository root to ``root``.

    ``git apply`` resolves diff paths against the repository root, not the
    current directory, so a module nested inside a larger checkout silently
    skips every file unless the prefix is supplied.
    """

    result = subprocess.run(
        ["git", "rev-parse", "--show-prefix"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _apply_with_git(root: Path, patch: str) -> None:
    prefix = _git_prefix(root)
    command = ["git", "apply", "--whitespace=nowarn"]
    if prefix:
        command.append(f"--directory={prefix}")
    check = subprocess.run(
        [*command, "--check", "-"],
        cwd=root,
        input=patch,
        text=True,
        capture_output=True,
    )
    if check.returncode != 0:
        raise RuntimeError(f"git apply rejected the patch: {check.stderr or check.stdout}")
    applied = subprocess.run(
        [*command, "-"],
        cwd=root,
        input=patch,
        text=True,
        capture_output=True,
    )
    if applied.returncode != 0:
        raise RuntimeError(f"git apply failed: {applied.stderr or applied.stdout}")


def _apply_with_patch(root: Path, patch: str) -> None:
    dry = subprocess.run(
        ["patch", "-p1", "--dry-run"],
        cwd=root,
        input=patch,
        text=True,
        capture_output=True,
    )
    if dry.returncode != 0:
        raise RuntimeError(f"patch did not apply: {dry.stderr or dry.stdout}")
    subprocess.run(["patch", "-p1"], cwd=root, input=patch, text=True, check=True)


def apply_patch(root: Path, patch: str) -> str:
    """Apply a normalized patch and verify the files actually changed.

    Both ``git apply`` and ``patch`` can report success while touching nothing,
    so the result is checked by comparing content hashes before and after.
    Returns the tool that applied it.
    """

    patch = repair_hunk_counts(patch)
    targets = _patch_paths(patch)
    before = _fingerprint(root, targets)

    tools = []
    if shutil.which("git"):
        tools.append(("git", _apply_with_git))
    if shutil.which("patch"):
        tools.append(("patch", _apply_with_patch))
    if not tools:
        raise RuntimeError("neither git nor patch is available to apply the diff")

    errors = []
    for name, apply in tools:
        try:
            apply(root, patch)
        except Exception as error:
            errors.append(f"{name}: {error}")
            continue
        if _fingerprint(root, targets) != before:
            return name
        errors.append(f"{name}: reported success but changed no file")
    raise RuntimeError("could not apply the patch; " + " | ".join(errors))


def replay_patch(settings: Settings) -> str:
    """Load the committed recording so a judge without a key can reproduce.

    The patch lives in ``patches/`` rather than ``evidence/`` because evidence
    directories are regenerated and git-ignored, and this file is an input.
    """

    candidates = [
        settings.root / "patches" / "recorded.patch",
        settings.evidence_dir / "minimax" / "replay.patch",
    ]
    for path in candidates:
        if path.is_file():
            return repair_hunk_counts(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"recorded patch not found in: {', '.join(str(p) for p in candidates)}")


def generate_patch(settings: Settings, task: str, context: dict, *, live: bool) -> str:
    if not live:
        return replay_patch(settings)
    content = MiniMaxClient(settings).chat(build_patch_messages(task, context), purpose="patch")
    return repair_hunk_counts(extract_patch(content))
