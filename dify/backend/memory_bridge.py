"""Lost & Found Bureau: validated HTTP adapter for PowerContext 1.4.1."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import unicodedata
import urllib.error
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

PC_BASE_URL = os.environ.get("PC_BASE_URL", "http://powercontext:18080").rstrip("/")
PC_TOKEN = os.environ.get("PC_TOKEN", "")
PC_SCOPE_ID = os.environ.get("PC_SCOPE_ID", "")
SCOPE_TITLE = os.environ.get("PC_SCOPE_TITLE", "忘事管理局 Lost & Found Bureau")
SCOPE_FILE = Path(os.environ.get("PC_SCOPE_FILE", "/data/scope.json"))
LOCK = threading.RLock()  # One bridge process, one shared demonstration scope.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
LOGGER = logging.getLogger("lostfound")
app = FastAPI(title="Lost & Found Memory Bridge", version="3.0.0")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


class RememberIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="memo", pattern="^memo$")

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = normalize(value)
        if not value:
            raise ValueError("text must contain non-whitespace characters")
        return value


class RecallIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = normalize(value)
        if not value:
            raise ValueError("query must contain non-whitespace characters")
        return value


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(PC_BASE_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if PC_TOKEN:
        req.add_header("Authorization", f"Bearer {PC_TOKEN}")
    try:
        with OPENER.open(req, timeout=15) as response:
            result = json.loads(response.read())
        if not isinstance(result, dict):
            raise ValueError("PowerContext response must be an object")
        return result
    except (OSError, ValueError, urllib.error.URLError) as error:
        LOGGER.warning("PowerContext request failed: %s %s (%s)", method, path, type(error).__name__)
        raise HTTPException(status_code=503, detail="PowerContext is unavailable or returned an invalid response") from error


def scope_id() -> str:
    global PC_SCOPE_ID
    with LOCK:
        if PC_SCOPE_ID:
            return PC_SCOPE_ID
        if SCOPE_FILE.exists():
            cached = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
            if cached.get("scope_id"):
                PC_SCOPE_ID = cached["scope_id"]
                return PC_SCOPE_ID
        created = request("POST", "/v1/scopes", {
            "title": SCOPE_TITLE,
            "summary": "忘事管理局单用户演示档案，跨 Dify 会话保存登记事项。",
            "idempotency_key": "lostfound-bureau-v2",
        })
        if not isinstance(created.get("scope_id"), str) or not created["scope_id"]:
            raise HTTPException(status_code=502, detail="PowerContext did not return a scope ID")
        PC_SCOPE_ID = created["scope_id"]
        SCOPE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp = SCOPE_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps({"scope_id": PC_SCOPE_ID}), encoding="utf-8")
        temp.replace(SCOPE_FILE)
        return PC_SCOPE_ID


def entries(scope: str) -> list[dict]:
    records, cursors = [], set()
    cursor = None
    while True:
        payload = {"scope_id": scope, "limit": 100}
        if cursor:
            payload["cursor"] = cursor
        page = request("POST", "/v1/memory/entries/list", payload)
        if not isinstance(page.get("entries"), list):
            raise HTTPException(status_code=502, detail="Invalid PowerContext entries response")
        records.extend(page["entries"])
        cursor = page.get("next_cursor")
        if not cursor:
            return records
        if cursor in cursors:
            raise HTTPException(status_code=502, detail="PowerContext pagination did not advance")
        cursors.add(cursor)


def artifact_id(record: dict) -> str:
    value = (record.get("artifact") or {}).get("artifact_id")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=502, detail="PowerContext did not return a memory artifact ID")
    return value


@app.get("/health")
def health():
    try:
        scope = scope_id()
        return {"ok": True, "scope_id": scope, "memories": len(entries(scope)), "version": "3.0.0"}
    except HTTPException as error:
        return JSONResponse(status_code=503, content={"ok": False, "error": error.detail})


@app.post("/remember")
def remember(body: RememberIn) -> dict:
    with LOCK:
        scope = scope_id()
        # Dedup against persisted PowerContext data, also after bridge/container restarts.
        for record in entries(scope):
            if record.get("kind") == body.kind and normalize(record.get("text", "")) == body.text:
                return {"ok": True, "duplicate": True, "memory_id": artifact_id(record), "text": body.text}
        result = request("POST", "/v1/memory/remember", {"scope_id": scope, "kind": body.kind, "text": body.text})
        records = result.get("records")
        if not isinstance(records, list) or not records:
            raise HTTPException(status_code=502, detail="PowerContext did not confirm the write")
        matching = [record for record in records if record.get("text") == body.text and record.get("kind") == body.kind]
        if not matching:
            raise HTTPException(status_code=502, detail="PowerContext returned a different memory")
        return {"ok": True, "duplicate": False, "memory_id": artifact_id(matching[0]), "text": body.text}


@app.post("/recall")
def recall(body: RecallIn) -> dict:
    result = request("POST", "/v1/memory/search", {"scope_id": scope_id(), "query": body.query, "limit": body.limit})
    if not isinstance(result.get("hits"), list):
        raise HTTPException(status_code=502, detail="Invalid PowerContext search response")
    memories = []
    for hit in result["hits"]:
        record = hit.get("memory", {})
        text = record.get("text")
        if not isinstance(text, str) or not text:
            raise HTTPException(status_code=502, detail="PowerContext returned a memory without text")
        memories.append({"memory_id": artifact_id(record), "text": text, "kind": record.get("kind"), "score": hit.get("score")})
    return {"ok": True, "memories": memories, "count": len(memories)}


from bureau import install_bureau
from model_proxy import router as model_router
install_bureau(app, request, entries, artifact_id)
app.include_router(model_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "19080")), workers=1)
