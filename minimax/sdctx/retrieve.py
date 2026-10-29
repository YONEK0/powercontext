"""从 seekdb / OceanBase 检索开发上下文。

检索分两路，都由数据库执行:

1. **全文检索** —— 使用 seekdb 的 ngram 全文索引 ``MATCH ... AGAINST``，
   对中文规格与代码标识符都有效；
2. **词项打分** —— 把任务描述拆成词项（英文标识符 + 中文二元组），
   用 ``LIKE`` 在数据库里做加权命中统计，作为全文检索的补充。

两路结果按 id 合并，输出带真实来源（文件路径、行号、内容摘要、sha256）。
"""

from __future__ import annotations

import re
from typing import Any

from .seekdb_store import SeekDBStore

LATIN_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MAX_TERMS = 16

# 每个检索目标：表名、标题列、正文列、额外加权列
TARGETS: dict[str, dict[str, Any]] = {
    "specs": {
        "table": "ctx_spec_item",
        "title": "title",
        "body": "content",
        "extra": ("item_id", "spec_title"),
        "select": "id, spec_path, spec_title, item_id, title, kind, version, content, sha256",
        "order": "spec_path, item_id",
    },
    "code": {
        "table": "ctx_code_symbol",
        "title": "qualified_name",
        "body": "content",
        "extra": ("symbol",),
        "select": "id, path, symbol, qualified_name, kind, start_line, end_line, content, sha256",
        "order": "path, start_line",
    },
    "tests": {
        "table": "ctx_test_case",
        "title": "test_name",
        "body": "content",
        "extra": ("qualified_name",),
        "select": "id, path, test_name, qualified_name, kind, start_line, end_line, content, sha256",
        "order": "path, start_line",
    },
}


def extract_terms(text: str) -> list[str]:
    """把任务描述拆成用于数据库匹配的词项。"""

    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip().lower()
        if len(term) >= 2 and term not in terms:
            terms.append(term)

    for token in LATIN_TOKEN_RE.findall(text):
        add(token)
        # 拆分 snake_case / camelCase，命中更多代码符号
        for part in re.split(r"[_\-\s]+", token):
            add(part)
        for part in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", token):
            add(part)

    cjk_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for run in cjk_runs:
        for index in range(len(run) - 1):
            add(run[index : index + 2])
        add(run)

    return terms[:MAX_TERMS]


def _like_clauses(target: dict[str, Any], terms: list[str]) -> tuple[str, list[Any], str, list[Any]]:
    """构造加权 LIKE 打分。

    返回 ``(score_expression, score_params, where_expression, where_params)``。
    """

    title_col = target["title"]
    body_col = target["body"]
    extra_cols = target["extra"]

    score_parts: list[str] = []
    score_params: list[Any] = []
    where_parts: list[str] = []
    where_params: list[Any] = []

    for term in terms:
        like = f"%{term}%"
        part = f"CASE WHEN {body_col} LIKE %s THEN 2 ELSE 0 END"
        score_params.append(like)
        part += f" + CASE WHEN {title_col} LIKE %s THEN 3 ELSE 0 END"
        score_params.append(like)
        for column in extra_cols:
            part += f" + CASE WHEN {column} LIKE %s THEN 2 ELSE 0 END"
            score_params.append(like)
        score_parts.append(f"({part})")

        where_parts.append(f"({body_col} LIKE %s OR {title_col} LIKE %s)")
        where_params.extend([like, like])
        for column in extra_cols:
            where_parts.append(f"({column} LIKE %s)")
            where_params.append(like)

    if not score_parts:
        return "0", [], "1=1", []
    return " + ".join(score_parts), score_params, " OR ".join(where_parts), where_params


def _fulltext_rows(store: SeekDBStore, target: dict[str, Any], query: str, limit: int) -> list[dict[str, Any]]:
    title_col = target["title"]
    body_col = target["body"]
    sql = (
        f"SELECT {target['select']}, "
        f"MATCH({title_col}, {body_col}) AGAINST (%s IN BOOLEAN MODE) AS ft_score "
        f"FROM {target['table']} "
        f"WHERE MATCH({title_col}, {body_col}) AGAINST (%s IN BOOLEAN MODE) "
        f"ORDER BY ft_score DESC, {target['order']} LIMIT %s"
    )
    try:
        return store.fetch_all(sql, (query, query, limit))
    except Exception:
        return []


def _like_rows(store: SeekDBStore, target: dict[str, Any], terms: list[str], limit: int) -> list[dict[str, Any]]:
    if not terms:
        return []
    score, score_params, where, where_params = _like_clauses(target, terms)
    sql = (
        f"SELECT {target['select']}, ({score}) AS like_score "
        f"FROM {target['table']} "
        f"WHERE {where} ORDER BY like_score DESC, {target['order']} LIMIT %s"
    )
    params = list(score_params) + list(where_params) + [limit]
    try:
        return store.fetch_all(sql, params)
    except Exception:
        return []


def retrieve(store: SeekDBStore, query: str, limits: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    """按任务描述检索规格、代码与测试。"""

    terms = extract_terms(query)
    results: dict[str, list[dict[str, Any]]] = {}
    for key, target in TARGETS.items():
        limit = limits.get(key, 5)
        merged: dict[str, dict[str, Any]] = {}
        for row in _fulltext_rows(store, target, query, limit * 3):
            row["score"] = float(row.pop("ft_score", 0.0))
            merged[str(row["id"])] = row
        for row in _like_rows(store, target, terms, limit * 3):
            score = float(row.pop("like_score", 0.0))
            key_id = str(row["id"])
            if key_id in merged:
                merged[key_id]["score"] = float(merged[key_id]["score"]) + score
            else:
                row["score"] = score
                merged[key_id] = row
        ordered = sorted(merged.values(), key=lambda item: (-float(item["score"]), item.get("id", "")))
        results[key] = ordered[:limit]
    results["terms"] = terms  # type: ignore[assignment]
    return results
