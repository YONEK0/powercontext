"""Configuration and local .env loading (no secrets are committed)."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER_MARKERS = ("path/to", "replace-with", "<", ">")


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def resolve_python(value: str | None) -> str:
    """Return a usable interpreter path.

    ``.env.example`` ships placeholder values so a fresh copy must not turn
    ``cli test`` into a ``FileNotFoundError``; an unusable value falls back to
    the interpreter running this module.
    """

    if not value:
        return sys.executable
    candidate = value.strip()
    if not candidate or any(marker in candidate for marker in PLACEHOLDER_MARKERS):
        return sys.executable
    path = Path(candidate)
    if path.is_file():
        return str(path)
    found = shutil.which(candidate)
    return found or sys.executable


def _resolve_path(root: Path, value: str | None, default: str) -> Path:
    """Resolve a configured path against the project root.

    An absolute value is used as-is; ``pathlib`` would otherwise glue it onto
    the root and turn ``/tmp/run/seekdb`` into ``<root>/tmp/run/seekdb``.
    """

    candidate = Path(value or default)
    if candidate.is_absolute():
        return candidate
    return root / candidate


@dataclass(frozen=True)
class Settings:
    root: Path
    seekdb_path: Path
    powercontext_db: Path
    evidence_dir: Path
    minimax_base_url: str
    minimax_api_key: str | None
    minimax_model: str
    minimax_timeout: float
    test_python: str
    change: str | None
    task: str | None

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        """Resolve the project root.

        Priority: explicit argument, ``MINIMAX_ROOT``, then the current working
        directory. The package location is *not* used, so the same install can
        be pointed at any repository. ``.env`` is read from the resolved root
        and then from the working directory, which keeps ``cd elsewhere && cli``
        working while still honouring a project-local .env.
        """

        if root is not None:
            resolved = Path(root)
        elif os.getenv("MINIMAX_ROOT"):
            resolved = Path(os.environ["MINIMAX_ROOT"])
        else:
            resolved = Path.cwd()
        resolved = resolved.resolve()
        _load_dotenv(Path.cwd() / ".env")
        _load_dotenv(resolved / ".env")
        evidence = resolved / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        return cls(
            root=resolved,
            seekdb_path=_resolve_path(resolved, os.getenv("MINIMAX_SEEKDB_PATH"), "data/seekdb"),
            powercontext_db=_resolve_path(resolved, os.getenv("POWERCONTEXT_DB"), "data/powercontext.db"),
            evidence_dir=evidence,
            minimax_base_url=os.getenv("MINIMAX_BASE_URL", "http://127.0.0.1:7863/v1").rstrip("/"),
            minimax_api_key=os.getenv("MINIMAX_API_KEY") or None,
            minimax_model=os.getenv("MINIMAX_MODEL", "cn:minimax-m3.1-flash"),
            minimax_timeout=float(os.getenv("MINIMAX_TIMEOUT_SECONDS", "120")),
            test_python=resolve_python(os.getenv("MINIMAX_TEST_PYTHON")),
            change=os.getenv("MINIMAX_CHANGE") or None,
            task=os.getenv("MINIMAX_TASK") or None,
        )
