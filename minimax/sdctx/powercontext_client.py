"""PowerContext HTTP 客户端（仅依赖标准库）。

PowerContext 是本作品使用的"任务状态"载体：

* **Source 日志** —— 写入完整任务状态快照（上下文、进度、验证结果），可按 id 读回;
* **Atomic Memory** —— 写入一条可检索的任务进展摘要，可在后续运行中搜索读回。

两个接口都实现幂等：同一个 ``source_id`` 重复写入不会产生重复证据。
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config


class PowerContextError(RuntimeError):
    """PowerContext 请求失败。"""

    def __init__(self, path: str, status: int, detail: str) -> None:
        super().__init__(f"PowerContext {path} 返回 HTTP {status}: {detail[:600]}")
        self.path = path
        self.status = status
        self.detail = detail


class PowerContextClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------ 底层
    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.pc_url}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.pc_token:
            headers["Authorization"] = f"Bearer {self.config.pc_token}"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.config.pc_timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise PowerContextError(path, error.code, detail) from error
        except URLError as error:
            raise PowerContextError(path, 0, f"无法连接 PowerContext: {error}") from error
        if not body.strip():
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request("POST", path, payload)

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    # ------------------------------------------------------------------ Scope
    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health/live")

    def create_scope(self, title: str, summary: str, idempotency_key: str) -> dict[str, Any]:
        return self.post(
            "/v1/scopes",
            {"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )

    def resolve_scope(self) -> str:
        """返回可用的 scope_id：优先使用配置，其次创建并缓存。"""

        if self.config.pc_scope:
            return self.config.pc_scope
        cache = self.config.home / "scope_id"
        if cache.exists():
            cached = cache.read_text(encoding="utf-8").strip()
            if cached:
                return cached
        created = self.create_scope(
            title="MiniMax SDD 上下文模块",
            summary="SDD 上下文模块的任务状态与验证结果",
            idempotency_key="sdctx-minimax",
        )
        scope_id = str(created.get("scope_id") or "")
        if not scope_id:
            raise PowerContextError("/v1/scopes", 500, f"创建 Scope 失败: {created}")
        self.config.home.mkdir(parents=True, exist_ok=True)
        cache.write_text(scope_id, encoding="utf-8")
        return scope_id

    # ------------------------------------------------------------------ Source
    def capture_content_source(self, scope_id: str, source_id: str, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return self.post(
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": source_id,
                "content": content,
                "metadata": metadata,
            },
        )

    def get_content_source(self, scope_id: str, source_id: str) -> dict[str, Any] | None:
        """读取一个 Content Source；不存在时返回 None（404）。"""

        try:
            return self.get(f"/v1/scopes/{scope_id}/sources/content/{source_id}")
        except PowerContextError as error:
            if error.status == 404:
                return None
            raise

    # ------------------------------------------------------------------ Memory
    def remember(self, scope_id: str, kind: str, text: str, reason: str) -> dict[str, Any]:
        return self.post(
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason},
        )

    def search_memory(self, scope_id: str, query: str, limit: int = 10) -> dict[str, Any]:
        return self.post(
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit},
        )

    def prepare_context(self, scope_id: str, query: str, max_bytes: int = 4000) -> dict[str, Any]:
        return self.post(
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )
