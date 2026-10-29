"""运行时配置。

所有配置项都可以用环境变量覆盖，默认值保证"零配置可跑通验收样例"。
环境变量清单见 ``docs/01-运行说明.md``。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent
MINIMAX_ROOT = PKG_ROOT.parent
DEFAULT_PROJECT_ROOT = MINIMAX_ROOT / "business_project"


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """一次运行所需要的全部配置。"""

    # 目录
    home: Path
    project_root: Path
    evidence_dir: Path

    # seekdb / OceanBase
    seekdb_mode: str  # embedded | server
    seekdb_path: Path
    seekdb_database: str
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_socket: str | None

    # PowerContext
    pc_url: str
    pc_scope: str | None
    pc_token: str | None
    pc_timeout: float

    # MiniMax
    minimax_base_url: str
    minimax_api_key: str | None
    minimax_model: str
    minimax_timeout: float
    minimax_max_tokens: int

    # 检索
    top_k_specs: int
    top_k_code: int
    top_k_tests: int

    # 验证：agent 落盘后自动运行的测试命令
    test_command: tuple[str, ...]
    max_code_candidates: int

    @classmethod
    def load(cls, **overrides: object) -> "Config":
        home = Path(_env_str("SDCTX_HOME", str(MINIMAX_ROOT / ".sdctx"))).expanduser().resolve()
        project_root = Path(
            _env_str("SDCTX_PROJECT_ROOT", str(DEFAULT_PROJECT_ROOT))
        ).expanduser().resolve()
        seekdb_mode = (_env_str("SDCTX_SEEKDB_MODE", "embedded") or "embedded").lower()
        config = cls(
            home=home,
            project_root=project_root,
            evidence_dir=Path(
                _env_str("SDCTX_EVIDENCE_DIR", str(MINIMAX_ROOT / "evidence"))
            ).expanduser().resolve(),
            seekdb_mode=seekdb_mode,
            seekdb_path=Path(
                _env_str("SDCTX_SEEKDB_PATH", str(home / "seekdb"))
            ).expanduser().resolve(),
            seekdb_database=_env_str("SDCTX_SEEKDB_DATABASE", "sdctx") or "sdctx",
            db_host=_env_str("SDCTX_DB_HOST", "127.0.0.1") or "127.0.0.1",
            db_port=_env_int("SDCTX_DB_PORT", 2881),
            db_user=_env_str("SDCTX_DB_USER", "root") or "root",
            db_password=_env_str("SDCTX_DB_PASSWORD", "") or "",
            db_socket=_env_str("SDCTX_DB_SOCKET", None),
            pc_url=(_env_str("SDCTX_PC_URL", "http://127.0.0.1:18080") or "").rstrip("/"),
            pc_scope=_env_str("SDCTX_PC_SCOPE", None),
            pc_token=_env_str("SDCTX_PC_TOKEN", None),
            pc_timeout=_env_float("SDCTX_PC_TIMEOUT", 30.0),
            minimax_base_url=(
                _env_str("MINIMAX_BASE_URL", "https://api.minimax.io/v1") or ""
            ).rstrip("/"),
            minimax_api_key=_env_str("MINIMAX_API_KEY", None),
            minimax_model=_env_str("MINIMAX_MODEL", "MiniMax-M3") or "MiniMax-M3",
            minimax_timeout=_env_float("MINIMAX_TIMEOUT", 300.0),
            minimax_max_tokens=_env_int("MINIMAX_MAX_TOKENS", 32768),
            top_k_specs=_env_int("SDCTX_TOP_K_SPECS", 8),
            top_k_code=_env_int("SDCTX_TOP_K_CODE", 8),
            top_k_tests=_env_int("SDCTX_TOP_K_TESTS", 8),
            test_command=tuple(
                part
                for part in _env_str(
                    "SDCTX_TEST_COMMAND", f"{sys.executable} -m pytest tests/ -q"
                ).split()
                if part
            ),
            max_code_candidates=_env_int("SDCTX_MAX_CODE_CANDIDATES", 14),
        )
        if overrides:
            config = replace(config, **overrides)  # type: ignore[arg-type]
        return config

    def describe(self) -> dict[str, object]:
        """返回可安全打印的配置摘要（不包含凭据）。"""

        return {
            "home": str(self.home),
            "project_root": str(self.project_root),
            "evidence_dir": str(self.evidence_dir),
            "seekdb_mode": self.seekdb_mode,
            "seekdb_path": str(self.seekdb_path) if self.seekdb_mode == "embedded" else None,
            "seekdb_database": self.seekdb_database,
            "db_endpoint": (
                f"{self.db_host}:{self.db_port}"
                if self.seekdb_mode == "server"
                else "embedded (unix socket)"
            ),
            "pc_url": self.pc_url,
            "pc_scope": self.pc_scope,
            "pc_token_set": bool(self.pc_token),
            "minimax_base_url": self.minimax_base_url,
            "minimax_model": self.minimax_model,
            "minimax_api_key_set": bool(self.minimax_api_key),
        }
