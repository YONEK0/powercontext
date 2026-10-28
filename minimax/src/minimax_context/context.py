"""Assemble a source-anchored development context."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Settings
from .db import SeekDB
from .retrieve import TaskPlan, build_plan
from .verify import probe_selectors

PROBE_LOG = "probe.log"


def _probe(settings: Settings, plan: TaskPlan) -> dict[str, object] | None:
    """Classify candidate tests by running them on the current code.

    This is what makes the acceptance test a measurement instead of a guess:
    the test that fails now is the one the change has to fix.
    """

    candidates = plan.candidate_tests
    if not candidates:
        return None
    log_path = settings.evidence_dir / "tests" / PROBE_LOG
    try:
        result = probe_selectors(settings.root, settings.test_python, candidates, log_path)
    except OSError:
        # A missing interpreter is an environment problem, not a test result;
        # context selection still works from ranking alone.
        return None
    plan.assign_tests(list(result["failed"]), list(result["passed"]))
    return result


def build_plan_for(settings: Settings, db: SeekDB, task: str) -> tuple[TaskPlan, dict[str, object] | None]:
    plan = build_plan(db, task)
    probe = _probe(settings, plan)
    return plan, probe


def context_from_plan(plan: TaskPlan, task: str, state: dict | None, probe: dict | None) -> dict:
    return {
        "schema": "minimax.sdd-context.v2",
        "task": task,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "state": state or {},
        "terms": plan.terms,
        "groups": {
            kind: [item.as_dict() for item in bucket]
            for kind, bucket in plan.groups.items()
        },
        "items": [item.as_dict() for item in plan.items],
        "target_files": plan.target_files,
        "candidate_tests": plan.candidate_tests,
        "target_tests": plan.target_tests,
        "regression_tests": plan.regression_tests,
        "probe": {
            "command": (probe or {}).get("command"),
            "exit_code": (probe or {}).get("exit_code"),
            "failed": (probe or {}).get("failed", []),
            "passed": (probe or {}).get("passed", []),
            "log": (probe or {}).get("log", ""),
        },
        "required": {
            "spec": [item.doc.doc_id for item in plan.groups.get("spec", [])],
            "code": [item.doc.doc_id for item in plan.groups.get("code", [])],
            "test": [item.doc.doc_id for item in plan.groups.get("test", [])],
        },
    }


def build_context(
    settings: Settings, db: SeekDB, task: str, state: dict | None = None
) -> dict:
    plan, probe = build_plan_for(settings, db, task)
    payload = context_from_plan(plan, task, state, probe)
    target = settings.evidence_dir / "contexts" / f"{int(time.time())}-context.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def render_for_agent(context: dict) -> str:
    """Render the selected sources as a compact prompt block."""

    lines = [
        "TASK:",
        context["task"],
        "",
        "STATE:",
        json.dumps(context.get("state", {}), ensure_ascii=False, sort_keys=True),
        "",
    ]
    if context.get("target_files"):
        lines.extend(["FILES IN SCOPE:", *context["target_files"], ""])
    if context.get("target_tests"):
        lines.extend(["ACCEPTANCE TEST:", *context["target_tests"], ""])
    if context.get("regression_tests"):
        lines.extend(["MUST KEEP PASSING:", *context["regression_tests"], ""])
    lines.append("SOURCES:")
    for kind in ("spec", "code", "test"):
        for item in context["groups"].get(kind, []):
            header = (
                f"--- {kind}: {item['path']}"
                + (f"#{item['symbol']}" if item.get("symbol") else "")
                + f" (lines {item['line_start']}-{item['line_end']}, version {item['version']})"
            )
            lines.extend((header, item["content"], ""))
    return "\n".join(lines)
