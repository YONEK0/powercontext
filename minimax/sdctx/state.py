"""任务状态的保存与复用（基于 PowerContext）。

状态分成两层:

* **Source 快照** —— 完整 JSON 状态（上下文、进度、验证结果），写在
  ``sdctx-state-<task_id>`` 这个稳定 id 上，随时可以读回;
* **Memory 摘要** —— 一条几百字以内的进展摘要，可用 ``/v1/memory/search``
  检索到，用于"重启后先回忆上次的进展"。

两个模块的流程刻意分开：写状态用 ``save``，读状态用 ``load``，
``load`` 只依赖 PowerContext，不依赖任何本地缓存文件。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .powercontext_client import PowerContextClient

STATE_MARKER = "SDCTX-STATE"
MAX_MEMORY_CHARS = 480


def make_task_id(task: str) -> str:
    """把任务描述转成稳定、可读的任务 id。"""

    digest = hashlib.sha256(task.encode("utf-8")).hexdigest()[:8]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", task).strip("-").lower()
    slug = "-".join(part for part in slug.split("-") if part)[:48]
    return f"{slug or 'task'}-{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _source_content(source_result: Any) -> Any:
    if not isinstance(source_result, dict):
        return None
    content = source_result.get("content")
    if content is None and isinstance(source_result.get("source"), dict):
        content = source_result["source"].get("content")
    return content


def _summarize(payload: dict[str, Any]) -> str:
    context = payload.get("context") or {}
    specs = context.get("specs") or []
    progress = payload.get("progress") or {}
    done = progress.get("done") or []
    todo = progress.get("todo") or []
    parts = [
        f"{STATE_MARKER} {payload.get('task_id')}",
        f"status={payload.get('status', 'unknown')}",
        f"specs={len(specs)}",
        f"code={len(context.get('code') or [])}",
        f"tests={len(context.get('tests') or [])}",
    ]
    if done:
        parts.append("done=" + ",".join(str(item) for item in done)[:120])
    if todo:
        parts.append("todo=" + ",".join(str(item) for item in todo)[:120])
    if payload.get("verification"):
        parts.append("verified=" + str(payload["verification"])[:120])
    text = " | ".join(parts)
    return text[:MAX_MEMORY_CHARS]


def save_state(
    client: PowerContextClient,
    task: str,
    context: dict[str, Any],
    progress: dict[str, Any] | None = None,
    verification: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """把一次任务的上下文与进展写入 PowerContext。"""

    scope_id = client.resolve_scope()
    resolved_task_id = task_id or make_task_id(task)
    payload: dict[str, Any] = {
        "schema": "sdctx.state.v1",
        "task_id": resolved_task_id,
        "task": task,
        "status": (progress or {}).get("status", "in_progress"),
        "progress": progress or {},
        "verification": verification,
        "context": context,
        "updated_at": _now(),
    }
    source_id = next_source_id(client, scope_id, resolved_task_id)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    source_result = client.capture_content_source(
        scope_id,
        source_id,
        body,
        {
            "origin": "sdctx",
            "task_id": resolved_task_id,
            "task": task,
            "schema": "sdctx.state.v1",
        },
    )
    memory_result = client.remember(
        scope_id,
        "progress",
        _summarize(payload),
        f"SDD 任务进展: {resolved_task_id}",
    )
    return {
        "scope_id": scope_id,
        "source_id": source_id,
        "task_id": resolved_task_id,
        "source": source_result,
        "memory": memory_result,
        "memory_summary": _summarize(payload),
    }


def next_source_id(client: PowerContextClient, scope_id: str, task_id: str) -> str:
    """Content Source 不可覆盖，因此每次保存使用新的修订号。"""

    revision = 1
    while revision < 1000:
        candidate = f"sdctx-state-{task_id}-r{revision}"
        if client.get_content_source(scope_id, candidate) is None:
            return candidate
        revision += 1
    raise RuntimeError(f"任务 {task_id} 的状态修订过多，无法继续写入")


def load_state(client: PowerContextClient, task_id: str) -> dict[str, Any]:
    """从 PowerContext 读回任务状态（不依赖本地缓存）。"""

    scope_id = client.resolve_scope()
    source_id = ""
    payload: dict[str, Any] | None = None
    content = None
    revision = 1
    while revision < 1000:
        candidate = f"sdctx-state-{task_id}-r{revision}"
        source_result = client.get_content_source(scope_id, candidate)
        if source_result is None:
            break
        source_id = candidate
        content = _source_content(source_result)
        if content is not None:
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                payload = None
        revision += 1

    source_result = {"content": content} if content is not None else None
    if isinstance(source_result, dict):
        content = source_result.get("content")
        if content is None and isinstance(source_result.get("source"), dict):
            content = source_result["source"].get("content")

    memory_hits: list[dict[str, Any]] = []
    try:
        search = client.search_memory(scope_id, task_id, limit=20)
        for hit in search.get("hits", []) if isinstance(search, dict) else []:
            memory = hit.get("memory") or {}
            text = str(memory.get("text", ""))
            if f"{STATE_MARKER} {task_id}" in text:
                memory_hits.append(hit)
        memory_hits.sort(
            key=lambda hit: int(((hit.get("memory") or {}).get("artifact") or {}).get("revision", 0)),
            reverse=True,
        )
    except Exception:
        memory_hits = []

    return {
        "scope_id": scope_id,
        "task_id": task_id,
        "source_id": source_id,
        "source_found": payload is not None,
        "memory_hits": memory_hits[:1],
        "memory_hit_count": len(memory_hits),
        "state": payload,
    }
