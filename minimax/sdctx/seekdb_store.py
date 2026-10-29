"""seekdb / OceanBase 存储层。

支持两种运行方式:

``embedded``
    通过 ``pylibseekdb`` 在本机启动一个嵌入式 OceanBase seekdb 实例，
    使用 Unix Domain Socket 连接。无需额外部署数据库进程。

``server``
    连接外部已经启动好的 seekdb 或 OceanBase（MySQL 协议），地址来自
    ``SDCTX_DB_HOST`` / ``SDCTX_DB_PORT`` / ``SDCTX_DB_USER`` / ``SDCTX_DB_PASSWORD``。

两种方式暴露给上层的是同一套 API：``execute`` / ``fetch_all`` / ``ensure_schema``。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymysql

from .config import Config

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS ctx_document (
        id CHAR(64) PRIMARY KEY,
        kind VARCHAR(16) NOT NULL,
        path VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL,
        content LONGTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        meta TEXT,
        updated_at DATETIME NOT NULL,
        UNIQUE KEY uk_ctx_document_path (path(190), kind)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ctx_spec_item (
        id CHAR(64) PRIMARY KEY,
        spec_path VARCHAR(512) NOT NULL,
        spec_title VARCHAR(512) NOT NULL,
        item_id VARCHAR(64) NOT NULL,
        title VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        version VARCHAR(32) NOT NULL DEFAULT '',
        content LONGTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        UNIQUE KEY uk_ctx_spec_item (spec_path(190), item_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ctx_code_symbol (
        id CHAR(64) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        symbol VARCHAR(256) NOT NULL,
        qualified_name VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        content LONGTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        KEY ix_ctx_code_path (path(190)),
        KEY ix_ctx_code_name (symbol(190))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS ctx_test_case (
        id CHAR(64) PRIMARY KEY,
        path VARCHAR(512) NOT NULL,
        test_name VARCHAR(256) NOT NULL,
        qualified_name VARCHAR(512) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        content LONGTEXT NOT NULL,
        sha256 CHAR(64) NOT NULL,
        updated_at DATETIME NOT NULL,
        KEY ix_ctx_test_path (path(190)),
        KEY ix_ctx_test_name (test_name(190))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
)

# 全文索引：seekdb 支持 ngram 分词器，中文规格可以被直接检索。
FULLTEXT_STATEMENTS: tuple[str, ...] = (
    "CREATE FULLTEXT INDEX ft_ctx_document ON ctx_document(title, content) WITH PARSER ngram",
    "CREATE FULLTEXT INDEX ft_ctx_spec_item ON ctx_spec_item(title, content) WITH PARSER ngram",
    "CREATE FULLTEXT INDEX ft_ctx_code_symbol ON ctx_code_symbol(qualified_name, content) WITH PARSER ngram",
    "CREATE FULLTEXT INDEX ft_ctx_test_case ON ctx_test_case(test_name, content) WITH PARSER ngram",
)


class SeekDBStore:
    """seekdb / OceanBase 的同步访问入口。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._connection: Any = None
        self._instance: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._database = config.seekdb_database

    # ------------------------------------------------------------------ 生命周期
    def __enter__(self) -> "SeekDBStore":
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def connect(self) -> None:
        if self.config.seekdb_mode == "server":
            self._connection = pymysql.connect(
                host=self.config.db_host,
                port=self.config.db_port,
                user=self.config.db_user,
                password=self.config.db_password,
                charset="utf8mb4",
                autocommit=True,
            )
            self._use_database(self._database)
            return

        import pylibseekdb  # 延迟导入：server 模式不需要嵌入式运行时

        self._loop = asyncio.new_event_loop()
        path = Path(self.config.seekdb_path)
        path.mkdir(parents=True, exist_ok=True)
        self._instance = self._loop.run_until_complete(pylibseekdb.aopen(str(path)))
        options = dict(self._instance.connection_options())
        socket = options.get("unix_socket") or self.config.db_socket
        self._connection = pymysql.connect(
            unix_socket=socket,
            user=str(options.get("user") or self.config.db_user),
            password=str(options.get("password") or ""),
            charset="utf8mb4",
            autocommit=True,
        )
        self._use_database(self._database)

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
        if self._instance is not None:
            try:
                self._instance.close()
            finally:
                self._instance = None
        if self._loop is not None:
            try:
                self._loop.close()
            finally:
                self._loop = None

    # ------------------------------------------------------------------ 基本操作
    def _use_database(self, database: str) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
            cursor.execute(f"USE `{database}`")

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.rowcount

    def fetch_all(self, sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(sql, params or ())
            columns = [column[0] for column in cursor.description or ()]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def fetch_one(self, sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def ensure_schema(self) -> list[str]:
        """建表 + 建全文索引，返回实际执行成功的索引语句。"""

        for statement in SCHEMA_STATEMENTS:
            self.execute(statement)
        created: list[str] = []
        for statement in FULLTEXT_STATEMENTS:
            try:
                self.execute(statement)
                created.append(statement.strip())
            except Exception as error:  # pragma: no cover - 取决于数据库版本
                message = str(error)
                if "exist" not in message.lower() and "duplicate" not in message.lower():
                    raise
        return created

    def reset(self) -> None:
        """清空全部索引数据（验证样例的"重置方法"）。"""

        for table in ("ctx_test_case", "ctx_code_symbol", "ctx_spec_item", "ctx_document"):
            self.execute(f"DELETE FROM {table}")

    def server_version(self) -> str:
        row = self.fetch_one("SELECT VERSION() AS version")
        return str(row["version"]) if row else "unknown"

    def row_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("ctx_document", "ctx_spec_item", "ctx_code_symbol", "ctx_test_case"):
            row = self.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = int(row["n"]) if row else 0
        return counts

    def query_log(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """执行一条只读查询并返回结果，用于生成"数据库查询记录"。"""

        return self.fetch_all(sql, tuple(params))
