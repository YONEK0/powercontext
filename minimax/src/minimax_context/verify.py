"""Run target and regression tests and preserve their logs.

Selectors come from the retrieved context, not from constants in this module:
the acceptance test is the highest-scoring test for the task and every other
selected test function is the regression gate.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
import time
from pathlib import Path

from .ingest import project_roots


def _code_version(root: Path, paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(set(paths)):
        path = root / rel
        if path.is_file():
            digest.update(rel.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12] if digest.digest() else ""


def _sandbox(root: Path, selector: str) -> str:
    """Return the directory pytest should run from for a selector.

    Selectors are repository-relative (``demo_app/tests/test_x.py::test_y``),
    so running from the repository root keeps the import path that the judge
    will use.
    """

    return str(root)


def run_pytest(
    root: Path,
    selectors: list[str],
    log_path: Path,
    python: str,
    *,
    label: str,
    scope: list[str],
) -> dict[str, object]:
    if not selectors:
        raise ValueError(f"no {label} selectors were selected from the context")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    command = [python, "-m", "pytest", "-q", *selectors]
    result = subprocess.run(
        command,
        cwd=_sandbox(root, selectors[0]),
        text=True,
        capture_output=True,
    )
    version = _code_version(root, scope)
    header = [
        f"$ {' '.join(shlex.quote(part) for part in command)}",
        f"cwd: {root}",
        f"exit_code: {result.returncode}",
        f"code_version: {version}",
        f"started_at: {started}",
        "",
    ]
    log_path.write_text(
        "\n".join(header) + f"{result.stdout}\n{result.stderr}", encoding="utf-8"
    )
    return {
        "label": label,
        "selectors": selectors,
        "command": command,
        "exit_code": result.returncode,
        "code_version": version,
        "started_at": started,
        "log": str(log_path.relative_to(root)),
    }


FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def probe_selectors(
    root: Path, python: str, selectors: list[str], log_path: Path
) -> dict[str, object]:
    """Run candidate tests on the current code and split them by outcome.

    This is how the acceptance test is identified without hard-coding it: on
    the initial code the unimplemented behaviour fails and the regression tests
    pass. If nothing fails the change is already present and the caller falls
    back to relevance ranking.
    """

    if not selectors:
        return {"failed": [], "passed": [], "exit_code": None, "log": ""}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [python, "-m", "pytest", "-q", "--tb=no", "-rf", *selectors]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True)
    failed = [match.strip() for match in FAILED_RE.findall(result.stdout + result.stderr)]
    failed_set = set(failed)
    passed = [selector for selector in selectors if selector not in failed_set]
    log_path.write_text(
        f"$ {' '.join(shlex.quote(part) for part in command)}\n"
        f"exit_code: {result.returncode}\n\n{result.stdout}\n{result.stderr}",
        encoding="utf-8",
    )
    return {
        "command": command,
        "exit_code": result.returncode,
        "failed": failed,
        "passed": passed,
        "log": str(log_path.relative_to(root)),
    }



def _log_path(evidence: Path, label: str) -> Path:
    return evidence / "tests" / f"{int(time.time())}-{label}.log"


def run_target(
    root: Path, evidence: Path, python: str, selectors: list[str], scope: list[str]
) -> dict[str, object]:
    return run_pytest(
        root,
        selectors,
        _log_path(evidence, "target"),
        python,
        label="target",
        scope=scope,
    )


def run_regression(
    root: Path, evidence: Path, python: str, selectors: list[str], scope: list[str]
) -> dict[str, object]:
    return run_pytest(
        root,
        selectors,
        _log_path(evidence, "regression"),
        python,
        label="regression",
        scope=scope,
    )
