"""Command-line entry points for the SDD context module."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys

from .agent import apply_patch, generate_patch
from .config import Settings
from .context import build_context
from .db import SeekDB
from .ingest import ingest
from .state import PowerContextState, default_state, merge_context, record
from .task import changes, resolve as resolve_task
from .verify import run_regression, run_target


def _db(settings: Settings) -> SeekDB:
    db = SeekDB(settings.seekdb_path, log_path=settings.evidence_dir / "seekdb_queries.jsonl")
    db.create_schema()
    return db


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _task(settings: Settings, args: argparse.Namespace) -> tuple[str, str | None]:
    return resolve_task(settings.root, args.task or settings.task, args.change or settings.change)


async def _load_state(settings: Settings, task: str) -> dict:
    return await PowerContextState(settings).load(task)


async def _save_state(settings: Settings, state: dict) -> dict:
    return await PowerContextState(settings).save(state)


def cmd_init(settings: Settings, args: argparse.Namespace) -> int:
    db = _db(settings)
    try:
        counts = ingest(settings.root, db, args.project)
        _print({"ingested": counts, "seekdb": str(settings.seekdb_path)})
    finally:
        db.close()
    return 0


def cmd_context(settings: Settings, args: argparse.Namespace) -> int:
    task, change = _task(settings, args)
    db = _db(settings)
    try:
        state = asyncio.run(_load_state(settings, task))
        context = build_context(settings, db, task, state)
        if change:
            state = merge_context(state, context)
            state = record(state, "context-selected", change=change)
            asyncio.run(_save_state(settings, state))
        _print(context)
    finally:
        db.close()
    return 0


def cmd_agent(settings: Settings, args: argparse.Namespace) -> int:
    task, change = _task(settings, args)
    db = _db(settings)
    try:
        state = asyncio.run(_load_state(settings, task))
        context = build_context(settings, db, task, state)
        patch = generate_patch(settings, task, context, live=args.live)
        patch_path = settings.evidence_dir / "minimax" / "last.patch"
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch, encoding="utf-8")

        applied_by = ""
        if args.apply:
            applied_by = apply_patch(settings.root, patch)

        state = merge_context(state, context)
        state = record(
            state,
            "patch-generated",
            mode="live" if args.live else "replay",
            applied=bool(args.apply),
            applied_by=applied_by,
            change=change,
        )
        state["patch"] = {
            "path": str(patch_path.relative_to(settings.root)),
            "mode": "live" if args.live else "replay",
            "applied": bool(args.apply),
            "applied_by": applied_by,
        }
        state["status"] = "patched" if args.apply else "patch-generated"
        state["verification"] = {}
        state["completed"] = sorted(set(state.get("completed", [])) | {"context", "minimax-patch"})
        state["todo"] = ["run target test", "run regression test"]
        state = asyncio.run(_save_state(settings, state))
        _print({"patch": str(patch_path), "applied": args.apply, "applied_by": applied_by, "state": state})
    finally:
        db.close()
    return 0


def cmd_test(settings: Settings, args: argparse.Namespace) -> int:
    task, change = _task(settings, args)
    state = asyncio.run(_load_state(settings, task))

    # Prefer the selection made while the change was still unimplemented: after
    # the patch is applied nothing fails, so probing again would lose the
    # acceptance test.
    targets = list(state.get("target_tests") or [])
    regression = list(state.get("regression_tests") or [])
    scope = list(state.get("target_files") or [])

    if not targets or not regression:
        db = _db(settings)
        try:
            context = build_context(settings, db, task, state)
        finally:
            db.close()
        targets = targets or list(context.get("target_tests") or [])
        regression = regression or list(context.get("regression_tests") or [])
        scope = scope or list(context.get("target_files") or [])
        state = merge_context(state, context)

    results = []
    if args.target:
        results.append(run_target(settings.root, settings.evidence_dir, settings.test_python, targets, scope))
    if args.regression:
        results.append(
            run_regression(settings.root, settings.evidence_dir, settings.test_python, regression, scope)
        )

    state["verification"] = {item["label"]: item for item in results}
    state["completed"] = sorted(set(state.get("completed", [])) | {"tests"})
    state["todo"] = []
    passed = all(item["exit_code"] == 0 for item in results)
    state["status"] = "verified" if passed else "test-failed"
    state = record(state, "tests-run", exit_codes={item["label"]: item["exit_code"] for item in results})
    state = asyncio.run(_save_state(settings, state))
    _print({"results": results, "state": state})
    return 0 if passed else 1


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    runtime = PowerContextState(settings)
    if args.all:
        _print(asyncio.run(runtime.list_tasks()))
        return 0
    task, change = _task(settings, args)
    _print(asyncio.run(runtime.load(task)))
    return 0


def cmd_tasks(settings: Settings, args: argparse.Namespace) -> int:
    _print(changes(settings.root))
    _print(asyncio.run(PowerContextState(settings).list_tasks()))
    return 0


def cmd_reset(settings: Settings, args: argparse.Namespace) -> int:
    if not args.yes:
        print("refusing to reset without --yes", file=sys.stderr)
        return 2
    for path in (settings.seekdb_path, settings.powercontext_db):
        target = settings.root / path if not path.is_absolute() else path
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    _print({"reset": ["seekdb", "powercontext"]})
    return 0


def cmd_demo(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(f"{args.app}:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_restore(settings: Settings, args: argparse.Namespace) -> int:
    source = settings.root / "evidence" / args.side / "app.py"
    if not source.is_file():
        raise FileNotFoundError(f"snapshot not found: {source}")
    target = settings.root / args.file
    shutil.copyfile(source, target)
    _print({"restored": args.side, "target": args.file})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minimax-context")
    parser.add_argument("--task", default=None, help="task statement; defaults to the OpenSpec change")
    parser.add_argument("--change", default=None, help="openspec change directory name")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create seekdb schema and ingest specs/code/tests")
    ingest_parser = sub.add_parser("ingest", help="re-ingest changed specs/code/tests")
    ingest_parser.add_argument("--project", action="append", help="project directory to ingest (repeatable)")
    sub.add_parser("context", help="print the selected development context")
    agent = sub.add_parser("agent", help="generate or replay a MiniMax patch")
    mode = agent.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="call MiniMax instead of replaying")
    mode.add_argument("--replay", action="store_true", help="use the recorded patch (default)")
    agent.add_argument("--apply", action="store_true", help="apply the generated patch")
    test = sub.add_parser("test", help="run the selected target and regression tests")
    test.add_argument("--target", action="store_true")
    test.add_argument("--regression", action="store_true")
    status = sub.add_parser("status", help="read task state from PowerContext")
    status.add_argument("--all", action="store_true", help="list saved tasks instead of one state")
    sub.add_parser("tasks", help="list OpenSpec changes and saved tasks")
    sub.add_parser("changes", help="list OpenSpec changes")
    restore = sub.add_parser("restore", help="restore a code snapshot")
    restore.add_argument("side", choices=("before", "after"))
    restore.add_argument("--file", default="demo_app/app.py")
    reset = sub.add_parser("reset", help="remove local seekdb and PowerContext data")
    reset.add_argument("--yes", action="store_true")
    demo = sub.add_parser("demo", help="run the demo HTTP service")
    demo.add_argument("--app", default="demo_app.app:app")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8000)
    demo.add_argument("--reload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = Settings.load()
    args = build_parser().parse_args(argv)
    if args.command in ("init", "ingest"):
        if not hasattr(args, "project"):
            args.project = None
        return cmd_init(settings, args)
    if args.command == "context":
        return cmd_context(settings, args)
    if args.command == "agent":
        return cmd_agent(settings, args)
    if args.command == "test":
        if not args.target and not args.regression:
            args.target = args.regression = True
        return cmd_test(settings, args)
    if args.command == "status":
        return cmd_status(settings, args)
    if args.command == "tasks":
        return cmd_tasks(settings, args)
    if args.command == "changes":
        _print(changes(settings.root))
        return 0
    if args.command == "restore":
        return cmd_restore(settings, args)
    if args.command == "reset":
        return cmd_reset(settings, args)
    if args.command == "demo":
        return cmd_demo(settings, args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
