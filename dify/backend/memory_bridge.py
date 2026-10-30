"""记仇小妖 · PowerContext 记忆桥

Dify 的 HTTP Request 节点不擅长处理鉴权、Scope 管理和 JSON 整形，这里用一个小服务
把 PowerContext 的记忆 API 包成两个傻瓜接口：

    POST /recall   {"query": "...", "limit": 5}      -> {"memories": [{"text": ..., "kind": ...}]}
    POST /remember {"text": "...", "kind": "prank"}  -> {"ok": true, "memory_id": "..."}

启动：
    pip install -r requirements.txt
    PC_BASE_URL=http://127.0.0.1:18080 python memory_bridge.py     # 默认监听 19080
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request
import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

PC_BASE_URL = os.environ.get("PC_BASE_URL", "http://127.0.0.1:18080").rstrip("/")
PC_TOKEN = os.environ.get("PC_TOKEN", "")
PC_SCOPE_ID = os.environ.get("PC_SCOPE_ID", "")
SCOPE_TITLE = os.environ.get("PC_SCOPE_TITLE", "记仇小妖 · GrudgeImp")
SCOPE_FILE = pathlib.Path(os.environ.get("PC_SCOPE_FILE", ".scope.json"))

app = FastAPI(title="GrudgeImp Memory Bridge", version="1.0.0")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{PC_BASE_URL}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    if PC_TOKEN:
        req.add_header("authorization", f"Bearer {PC_TOKEN}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode() or "{}"
        return json.loads(body)


def _scope_id() -> str:
    """优先用环境变量，其次读本地缓存，最后在 PowerContext 里找/建一个。"""
    global PC_SCOPE_ID
    if PC_SCOPE_ID:
        return PC_SCOPE_ID
    if SCOPE_FILE.exists():
        cached = json.loads(SCOPE_FILE.read_text()).get("scope_id")
        if cached:
            PC_SCOPE_ID = cached
            return cached

    # 先找同名 scope，避免重复创建
    try:
        listing = _request("GET", "/v1/scopes")
        for item in listing.get("items", listing if isinstance(listing, list) else []):
            if item.get("title") == SCOPE_TITLE:
                PC_SCOPE_ID = item["scope_id"]
                SCOPE_FILE.write_text(json.dumps({"scope_id": PC_SCOPE_ID}))
                return PC_SCOPE_ID
    except Exception:  # noqa: BLE001 - 列表接口不可用时直接尝试创建
        pass

    created = _request("POST", "/v1/scopes", {
        "title": SCOPE_TITLE,
        "summary": "记仇小妖应用的全部记忆：玩家做过的恶作剧、说过的梗与角色反应。",
        "idempotency_key": "grudge-imp-v1",
    })
    PC_SCOPE_ID = created["scope_id"]
    SCOPE_FILE.write_text(json.dumps({"scope_id": PC_SCOPE_ID}))
    return PC_SCOPE_ID


class RecallIn(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class RememberIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="prank")
    reason: str | None = None


@app.get("/health")
def health() -> dict:
    info = {"ok": True, "pc_base_url": PC_BASE_URL, "scope_id": None, "memories": None}
    try:
        scope = _scope_id()
        info["scope_id"] = scope
        listing = _request("POST", "/v1/memory/entries/list", {"scope_id": scope, "limit": 100})
        info["memories"] = len(listing.get("entries") or [])
    except Exception as error:  # noqa: BLE001
        info["ok"] = False
        info["error"] = str(error)
    return info


@app.post("/recall")
def recall(body: RecallIn) -> dict:
    scope = _scope_id()
    result = _request("POST", "/v1/memory/search", {
        "scope_id": scope, "query": body.query, "limit": body.limit,
    })
    memories = []
    for hit in result.get("hits", []):
        record = hit.get("memory", {})
        memories.append({
            "text": record.get("text", ""),
            "kind": record.get("kind", ""),
            "score": round(float(hit.get("score", 0)), 4),
        })
    return {"memories": memories, "count": len(memories)}


@app.post("/remember")
def remember(body: RememberIn) -> dict:
    scope = _scope_id()
    payload = {"scope_id": scope, "kind": body.kind, "text": body.text}
    if body.reason:
        payload["reason"] = body.reason
    result = _request("POST", "/v1/memory/remember", payload)
    memory = result.get("memory", result)
    return {"ok": True, "memory_id": memory.get("memory_id") or memory.get("id") or str(uuid.uuid4())}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "19080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
