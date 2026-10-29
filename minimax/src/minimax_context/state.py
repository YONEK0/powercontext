"""PowerContext-backed task state.

The module deliberately uses the published ``powercontext`` package rather than
copying a PowerContext checkout. State is stored as a Memory entry and is read
back by a fresh Runtime instance, which is the restartable handoff path.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings

STATE_KIND = "minimax-task-state"
SCOPE_TITLE = "MiniMax SDD"
SCOPE_SUMMARY = "Spec-driven development validation state"
SCOPE_KEY = "minimax-sdd-validation"
STATE_SCHEMA = "minimax.sdd-state.v2"


class StateUnavailable(RuntimeError):
    """PowerContext is not installed or the runtime could not be opened."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def default_state(task: str) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "task": task,
        "status": "not-started",
        "created_at": _now(),
        "updated_at": _now(),
        "terms": [],
        "selected_sources": {},
        "target_files": [],
        "target_tests": [],
        "regression_tests": [],
        "patch": {},
        "verification": {},
        "completed": [],
        "todo": [
            "select effective spec/code/tests",
            "generate a MiniMax patch",
            "run target and regression tests",
        ],
        "history": [],
    }


def merge_context(state: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Copy the plan into state so later runs can act without a fresh query."""

    merged = {
        **state,
        "terms": list(context.get("terms") or []),
        "selected_sources": dict(context.get("required") or {}),
        "target_files": list(context.get("target_files") or []),
        "target_tests": list(context.get("target_tests") or []),
        "regression_tests": list(context.get("regression_tests") or []),
    }
    return merged


def record(state: dict[str, Any], event: str, **fields: Any) -> dict[str, Any]:
    entry = {"at": _now(), "event": event, **fields}
    history = list(state.get("history") or [])
    history.append(entry)
    return {**state, "history": history, "updated_at": _now()}


@dataclass
class PowerContextState:
    settings: Settings

    def _url(self) -> str:
        self.settings.powercontext_db.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{self.settings.powercontext_db}"

    async def _runtime(self):
        try:
            from powercontext.builtin.persistence.sqlite import SQLiteConfig
            from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
        except ImportError as error:  # pragma: no cover - dependency error path
            raise StateUnavailable("powercontext is not installed") from error
        return open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=self._url())))

    async def save(self, state: dict[str, Any]) -> dict[str, Any]:
        from powercontext.builtin.artifacts.memory import MemoryEntryInput
        from powercontext.builtin.runtime import RememberMemoryRequest
        from powercontext.builtin.scope import ScopeDraft

        state = {**state, "updated_at": _now()}
        text = json.dumps(state, ensure_ascii=False, sort_keys=True)
        async with await self._runtime() as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title=SCOPE_TITLE, summary=SCOPE_SUMMARY, idempotency_key=SCOPE_KEY)
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind=STATE_KIND, text=text),))
            )
        self._mirror(state)
        return state

    def _mirror(self, state: dict[str, Any]) -> None:
        mirror = self.settings.evidence_dir / "state"
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / f"{int(time.time())}-state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    async def _entries(self) -> list[dict[str, Any]]:
        from powercontext.builtin.scope import ScopeDraft

        async with await self._runtime() as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title=SCOPE_TITLE, summary=SCOPE_SUMMARY, idempotency_key=SCOPE_KEY)
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            page = await memory.list()
        entries: list[dict[str, Any]] = []
        for record_ in page.entries:
            if record_.entry.kind != STATE_KIND:
                continue
            try:
                value = json.loads(record_.entry.text)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                entries.append(value)
        return entries

    async def load(self, task: str) -> dict[str, Any]:
        candidates = [value for value in await self._entries() if value.get("task") == task]
        if not candidates:
            return default_state(task)
        return max(candidates, key=lambda value: value.get("updated_at", ""))

    async def load_latest(self) -> dict[str, Any] | None:
        entries = await self._entries()
        if not entries:
            return None
        return max(entries, key=lambda value: value.get("updated_at", ""))

    async def list_tasks(self) -> list[dict[str, Any]]:
        """Return the newest state per task, most recent first."""

        newest: dict[str, dict[str, Any]] = {}
        for value in await self._entries():
            task = value.get("task") or ""
            current = newest.get(task)
            if current is None or value.get("updated_at", "") > current.get("updated_at", ""):
                newest[task] = value
        return sorted(newest.values(), key=lambda value: value.get("updated_at", ""), reverse=True)
