"""MiniMax API 客户端（OpenAI 兼容接口）。

只使用标准库 ``urllib``，方便在最小环境里复现。

MiniMax-M3 会在正文前输出 ``<think>...</think>`` 推理段，
``extract_content`` 会把它剥离，只保留最终答案。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
OPEN_THINK_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)


class MiniMaxError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass
class MiniMaxTurn:
    """一次模型调用的完整记录，用于生成"技术使用记录"。"""

    request: dict[str, Any]
    response_text: str
    raw_response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.request.get("model"),
            "base_url": self.request.get("base_url"),
            "messages": self.request.get("messages"),
            "response_text": self.response_text,
            "usage": self.usage,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "raw_response": self.raw_response,
        }


def extract_content(raw: str) -> str:
    """剥离 MiniMax 的推理段，返回最终正文。"""

    text = THINK_RE.sub("", raw or "")
    if "<think>" in text:
        text = OPEN_THINK_RE.sub("", text)
    return text.strip()


class MiniMaxClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.turns: list[MiniMaxTurn] = []

    @property
    def available(self) -> bool:
        return bool(self.config.minimax_api_key)

    def chat(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> MiniMaxTurn:
        if not self.config.minimax_api_key:
            raise MiniMaxError(
                "未配置 MINIMAX_API_KEY，无法调用 MiniMax。请先导出该环境变量。"
            )
        payload: list[dict[str, str]] = []
        if system:
            payload.append({"role": "system", "content": system})
        payload.extend(messages)

        body = {
            "model": self.config.minimax_model,
            "messages": payload,
            "max_tokens": max_tokens or self.config.minimax_max_tokens,
            "temperature": temperature,
        }
        request_record = {
            "model": body["model"],
            "base_url": self.config.minimax_base_url,
            "messages": payload,
        }

        url = f"{self.config.minimax_base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.minimax_api_key}",
        }
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        started = time.time()
        try:
            with urlopen(request, timeout=self.config.minimax_timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise MiniMaxError(f"MiniMax 调用失败 HTTP {error.code}: {detail[:800]}", error.code, detail) from error
        except URLError as error:
            raise MiniMaxError(f"无法连接 MiniMax: {error}") from error
        elapsed = time.time() - started

        parsed = json.loads(raw)
        choices = parsed.get("choices") or []
        text = ""
        if choices:
            text = str((choices[0].get("message") or {}).get("content") or "")
        turn = MiniMaxTurn(
            request=request_record,
            response_text=extract_content(text),
            raw_response=parsed,
            usage=parsed.get("usage") or {},
            elapsed_seconds=elapsed,
        )
        self.turns.append(turn)
        return turn
