"""Collect the artifacts of one validation run into evidence/final/.

Run by ``scripts/validate.sh``; the intermediate JSON comes from that run's
/tmp files, so every file here belongs to the same execution.
"""

from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "evidence" / "final"
TMP = Path("/tmp")


def _load(name: str) -> dict:
    return json.loads((TMP / name).read_text(encoding="utf-8"))


def main() -> None:
    FINAL.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.log", "*.json", "*.jsonl", "*.patch"):
        for old in FINAL.glob(pattern):
            old.unlink()

    before = _load("validate-before.json")
    shutil.copyfile(ROOT / before["results"][0]["log"], FINAL / "target-before.log")

    after = _load("validate-after.json")
    for item in after["results"]:
        shutil.copyfile(ROOT / item["log"], FINAL / f"{item['label']}-after.log")

    # Context is captured after the tests so the embedded state shows the
    # finished run: what was completed, what is left, and the test results.
    shutil.copyfile(TMP / "validate-context-final.json", FINAL / "context.json")
    shutil.copyfile(TMP / "validate-status.json", FINAL / "state-status.json")

    queries = ROOT / "evidence" / "seekdb_queries.jsonl"
    if queries.is_file():
        shutil.copyfile(queries, FINAL / "seekdb_queries.jsonl")

    records = sorted((ROOT / "evidence" / "minimax").glob("*-patch.json"))
    if records:
        shutil.copyfile(records[-1], FINAL / "minimax-patch.json")
    shutil.copyfile(ROOT / "evidence" / "minimax" / "last.patch", FINAL / "patch.diff")

    # before/after snapshots are kept side by side with the difference
    shutil.copyfile(ROOT / "evidence" / "before" / "app.py", FINAL / "app-before.py")
    shutil.copyfile(ROOT / "evidence" / "after" / "app.py", FINAL / "app-after.py")
    before_text = (ROOT / "evidence" / "before" / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    after_text = (ROOT / "evidence" / "after" / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    (FINAL / "code-diff.patch").write_text(
        "".join(
            difflib.unified_diff(
                before_text, after_text,
                fromfile="a/demo_app/app.py", tofile="b/demo_app/app.py",
            )
        ),
        encoding="utf-8",
    )

    for path in sorted(FINAL.iterdir()):
        print(f"    {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
