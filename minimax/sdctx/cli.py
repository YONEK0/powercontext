"""``python -m sdctx`` 命令行入口。

子命令一览::

    init                 初始化本地目录、seekdb 表结构与 PowerContext Scope
    ingest               把业务工程的规格/代码/测试导入 seekdb
    context              检索并组装开发上下文
    state save           把任务上下文与进展写入 PowerContext
    state load           从 PowerContext 读回任务状态（跨进程复用）
    patch                用 MiniMax 生成并落地一次代码修改
    search               直接在 seekdb 里做一次检索（用于留档数据库查询记录）
    doctor               体检：依赖、seekdb、PowerContext、MiniMax
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .agent import generate_change, write_evidence
from .config import Config
from .context import build_context, render_markdown
from .ingest import ingest_project
from .minimax_client import MiniMaxClient
from .powercontext_client import PowerContextClient
from .seekdb_store import SeekDBStore
from .state import load_state, make_task_id, save_state


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _split_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        result.extend(part.strip() for part in value.split(",") if part.strip())
    return result


# --------------------------------------------------------------------------- init
def cmd_init(args: argparse.Namespace, config: Config) -> int:
    config.home.mkdir(parents=True, exist_ok=True)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    client = PowerContextClient(config)
    with SeekDBStore(config) as store:
        indexes = store.ensure_schema()
        scope_id = client.resolve_scope()
        summary = {
            "version": __version__,
            "config": config.describe(),
            "database_engine": store.server_version(),
            "schema_ready": True,
            "fulltext_indexes": len(indexes),
            "powercontext_scope": scope_id,
        }
    _print_json(summary)
    return 0


# ------------------------------------------------------------------------- ingest
def cmd_ingest(args: argparse.Namespace, config: Config) -> int:
    project_root = Path(args.project).expanduser().resolve() if args.project else config.project_root
    with SeekDBStore(config) as store:
        store.ensure_schema()
        if args.reset:
            store.reset()
        summary = ingest_project(store, project_root)
        payload = {
            "project_root": str(project_root),
            "engine": store.server_version(),
            "counts": store.row_counts(),
            "ingest": summary.as_dict(),
        }
    _print_json(payload)
    return 0


# ------------------------------------------------------------------------ context
def _build_context(config: Config, task: str, task_id: str | None, with_state: bool = True) -> dict[str, Any]:
    client = PowerContextClient(config)
    with SeekDBStore(config) as store:
        store.ensure_schema()
        return build_context(store, client, config, task, task_id=task_id, with_state=with_state)


def cmd_context(args: argparse.Namespace, config: Config) -> int:
    context = _build_context(config, args.task, args.task_id, with_state=not args.no_state)
    if args.out:
        target = Path(args.out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"context written: {target}")
    if args.markdown:
        print(render_markdown(context))
    else:
        _print_json(
            {
                "task_id": context["task_id"],
                "summary": context["summary"],
                "database": context["database"],
                "progress": context["progress"],
            }
        )
    return 0


# -------------------------------------------------------------------------- state
def cmd_state_save(args: argparse.Namespace, config: Config) -> int:
    context = _build_context(config, args.task, args.task_id)
    client = PowerContextClient(config)
    progress = {
        "status": args.status,
        "done": _split_list(args.done),
        "todo": _split_list(args.todo),
    }
    result = save_state(
        client,
        args.task,
        context,
        progress=progress,
        verification=args.verification,
        task_id=context["task_id"],
    )
    _print_json(
        {
            "scope_id": result["scope_id"],
            "source_id": result["source_id"],
            "task_id": result["task_id"],
            "memory_summary": result["memory_summary"],
            "memory_records": (result["memory"] or {}).get("records"),
        }
    )
    return 0


def cmd_state_load(args: argparse.Namespace, config: Config) -> int:
    client = PowerContextClient(config)
    task_id = args.task_id or make_task_id(args.task or "")
    result = load_state(client, task_id)
    _print_json(
        {
            "scope_id": result["scope_id"],
            "task_id": result["task_id"],
            "source_id": result["source_id"],
            "source_found": result["source_found"],
            "memory_hits": len(result["memory_hits"]),
            "state": result["state"],
        }
    )
    return 0 if result["source_found"] else 2


# --------------------------------------------------------------------------- patch
def cmd_patch(args: argparse.Namespace, config: Config) -> int:
    context = _build_context(config, args.task, args.task_id)
    minimax = MiniMaxClient(config)
    result = generate_change(minimax, config, context, args.task, apply=not args.no_apply)
    if args.out:
        target = Path(args.out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"minimax turn written: {target}")
    _print_json(
        {
            "model": result.get("model"),
            "applied": result.get("applied"),
            "changed_files": result.get("changed_files"),
            "error": result.get("error"),
            "usage": result.get("usage"),
            "elapsed_seconds": result.get("elapsed_seconds"),
        }
    )
    return 0 if result.get("applied") else 1


# --------------------------------------------------------------------------- search
def cmd_search(args: argparse.Namespace, config: Config) -> int:
    from .retrieve import retrieve

    with SeekDBStore(config) as store:
        store.ensure_schema()
        hits = retrieve(
            store,
            args.query,
            {
                "specs": args.limit,
                "code": args.limit,
                "tests": args.limit,
            },
        )
        terms = hits.pop("terms", [])
        _print_json(
            {
                "engine": store.server_version(),
                "query": args.query,
                "terms": terms,
                "specs": hits["specs"],
                "code": hits["code"],
                "tests": hits["tests"],
            }
        )
    return 0


# --------------------------------------------------------------------------- sql
def cmd_sql(args: argparse.Namespace, config: Config) -> int:
    """在 seekdb / OceanBase 上直接执行一条只读 SQL，便于人工核对数据。"""

    with SeekDBStore(config) as store:
        store.ensure_schema()
        rows = store.query_log(args.query)
        _print_json(
            {
                "engine": store.server_version(),
                "sql": args.query,
                "row_count": len(rows),
                "rows": rows,
            }
        )
    return 0


# --------------------------------------------------------------------------- doctor
def cmd_doctor(args: argparse.Namespace, config: Config) -> int:
    report: dict[str, Any] = {"version": __version__, "config": config.describe()}
    try:
        with SeekDBStore(config) as store:
            store.ensure_schema()
            report["database"] = {
                "ok": True,
                "engine": store.server_version(),
                "counts": store.row_counts(),
            }
    except Exception as error:
        report["database"] = {"ok": False, "error": str(error)}
    client = PowerContextClient(config)
    try:
        health = client.health()
        report["powercontext"] = {
            "ok": True,
            "url": config.pc_url,
            "health": health,
            "scope_id": client.resolve_scope(),
        }
    except Exception as error:
        report["powercontext"] = {"ok": False, "url": config.pc_url, "error": str(error)}
    report["minimax"] = {
        "configured": bool(config.minimax_api_key),
        "base_url": config.minimax_base_url,
        "model": config.minimax_model,
    }
    _print_json(report)
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sdctx", description="SDD 上下文模块")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="初始化目录、seekdb 表结构与 Scope")
    p_init.set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="导入业务工程资料到 seekdb")
    p_ingest.add_argument("--project", help="业务工程根目录")
    p_ingest.add_argument("--reset", action="store_true", help="导入前清空已有索引")
    p_ingest.set_defaults(func=cmd_ingest)

    p_context = sub.add_parser("context", help="检索并组装开发上下文")
    p_context.add_argument("--task", required=True, help="任务描述")
    p_context.add_argument("--task-id", help="指定任务 id")
    p_context.add_argument("--out", help="把上下文 JSON 写到文件")
    p_context.add_argument("--markdown", action="store_true", help="输出 Markdown")
    p_context.add_argument("--no-state", action="store_true", help="不读取 PowerContext 状态")
    p_context.set_defaults(func=cmd_context)

    p_state = sub.add_parser("state", help="任务状态的保存与复用")
    state_sub = p_state.add_subparsers(dest="state_command", required=True)

    p_save = state_sub.add_parser("save", help="写入 PowerContext")
    p_save.add_argument("--task", required=True)
    p_save.add_argument("--task-id")
    p_save.add_argument("--status", default="in_progress")
    p_save.add_argument("--done", action="append")
    p_save.add_argument("--todo", action="append")
    p_save.add_argument("--verification")
    p_save.set_defaults(func=cmd_state_save)

    p_load = state_sub.add_parser("load", help="从 PowerContext 读回")
    p_load.add_argument("--task")
    p_load.add_argument("--task-id")
    p_load.set_defaults(func=cmd_state_load)

    p_patch = sub.add_parser("patch", help="用 MiniMax 生成并落地代码修改")
    p_patch.add_argument("--task", required=True)
    p_patch.add_argument("--task-id")
    p_patch.add_argument("--out", help="把模型调用记录写到文件")
    p_patch.add_argument("--no-apply", action="store_true", help="只生成补丁，不写文件")
    p_patch.set_defaults(func=cmd_patch)

    p_search = sub.add_parser("search", help="直接在 seekdb 中检索")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--limit", type=int, default=5)
    p_search.set_defaults(func=cmd_search)

    p_sql = sub.add_parser("sql", help="直接在数据库上执行一条只读 SQL")
    p_sql.add_argument("--query", required=True)
    p_sql.set_defaults(func=cmd_sql)

    p_doctor = sub.add_parser("doctor", help="环境体检")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config.load()
    return int(args.func(args, config))


if __name__ == "__main__":
    sys.exit(main())
