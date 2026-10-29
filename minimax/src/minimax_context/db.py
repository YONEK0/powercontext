"""Seekdb-backed storage and keyword index for engineering documents."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import pylibseekdb
except ImportError:  # pragma: no cover - dependency is declared in requirements
    pylibseekdb = None  # type: ignore[assignment]

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_PART = re.compile(r"[_\-./]+")
_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class Doc:
    doc_id: str
    kind: str
    path: str
    symbol: str | None
    content: str
    version: str
    content_hash: str
    line_start: int
    line_end: int
    metadata: dict[str, Any]

    @property
    def terms(self) -> set[str]:
        return tokenize(self.content)


def tokenize(text: str) -> set[str]:
    """Split text into index terms without a domain dictionary.

    Identifiers are indexed whole *and* by their parts so `min_price` is
    reachable from `price`, and CJK runs contribute bigrams so a Chinese task
    statement is searchable without a segmenter.
    """

    terms: set[str] = set()
    for raw in _WORD.findall(text):
        lowered = raw.lower()
        terms.add(lowered)
        for part in _PART.split(lowered):
            if len(part) >= 2:
                terms.add(part)
        for part in _CAMEL.findall(raw):
            lowered_part = part.lower()
            if len(lowered_part) >= 2:
                terms.add(lowered_part)
    for run in _CJK.findall(text):
        if len(run) == 1:
            terms.add(run)
        for index in range(len(run) - 1):
            terms.add(run[index : index + 2])
    return {term for term in terms if len(term) >= 2 or _CJK.match(term)}


def _sql_literal(value: str | int | float | None) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


class SeekDB:
    """Small SQL adapter; seekdb stores rows and the inverted term index."""

    def __init__(self, path: Path, *, log_path: Path | None = None) -> None:
        if pylibseekdb is None:
            raise RuntimeError("pylibseekdb is required; install requirements.txt")
        self.path = Path(path)
        # seekdb creates the database directory but not the directories above
        # it, so a nested path like /tmp/run/db/seekdb needs its parents.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.mkdir(parents=True, exist_ok=True)
        self.log_path = log_path
        self._instance = pylibseekdb.open(str(self.path))
        self._conn = pylibseekdb.connect("test", autocommit=True)
        self._cursor = self._conn.cursor()

    def close(self) -> None:
        try:
            self._cursor.close()
        finally:
            self._conn.close()

    def _execute(self, sql: str, *, log: bool = True) -> list[tuple[Any, ...]]:
        self._cursor.execute(sql)
        try:
            rows: list[tuple[Any, ...]] = list(self._cursor.fetchall())
        except Exception:
            rows = []
        if log:
            self._log(sql, rows)
        return rows

    def _log(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sql": sql,
            "row_count": len(list(rows)),
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def create_schema(self) -> None:
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS docs (
                doc_id VARCHAR(384) PRIMARY KEY,
                kind VARCHAR(16) NOT NULL,
                path VARCHAR(768) NOT NULL,
                symbol VARCHAR(384) NULL,
                content TEXT NOT NULL,
                version VARCHAR(64) NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                line_start INT NOT NULL,
                line_end INT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """,
            log=False,
        )
        self._execute(
            """
            CREATE TABLE IF NOT EXISTS doc_terms (
                term_id VARCHAR(512) PRIMARY KEY,
                doc_id VARCHAR(384) NOT NULL,
                term VARCHAR(128) NOT NULL
            )
            """,
            log=False,
        )
        try:
            self._execute("CREATE INDEX idx_doc_terms_term ON doc_terms (term)", log=False)
        except Exception:
            # seekdb versions differ on IF NOT EXISTS/index DDL; the term table
            # remains correct even when this optional index already exists.
            pass

    def replace_docs(self, docs: Iterable[Doc]) -> int:
        self._execute("DELETE FROM doc_terms", log=False)
        self._execute("DELETE FROM docs", log=False)
        documents = list(docs)
        # Autocommit makes one round trip per statement expensive on a real
        # repository, so rows are inserted in multi-row VALUES batches.
        doc_rows: list[str] = []
        term_rows: list[str] = []
        for doc in documents:
            doc_rows.append(
                "("
                + ",".join(
                    (
                        _sql_literal(doc.doc_id),
                        _sql_literal(doc.kind),
                        _sql_literal(doc.path),
                        _sql_literal(doc.symbol),
                        _sql_literal(doc.content),
                        _sql_literal(doc.version),
                        _sql_literal(doc.content_hash),
                        _sql_literal(doc.line_start),
                        _sql_literal(doc.line_end),
                        _sql_literal(json.dumps(doc.metadata, ensure_ascii=False, sort_keys=True)),
                    )
                )
                + ")"
            )
            for term in doc.terms:
                term_id = hashlib.sha256(f"{doc.doc_id}\x00{term}".encode()).hexdigest()
                term_rows.append(
                    "("
                    + _sql_literal(term_id)
                    + ","
                    + _sql_literal(doc.doc_id)
                    + ","
                    + _sql_literal(term)
                    + ")"
                )
        self._insert_batches("docs", doc_rows)
        self._insert_batches("doc_terms", term_rows)
        self._log("REPLACE docs/doc_terms", ())
        return len(documents)

    def _insert_batches(self, table: str, rows: list[str], *, size: int = 200) -> None:
        for start in range(0, len(rows), size):
            chunk = rows[start : start + size]
            self._execute(
                f"INSERT INTO {table} VALUES " + ",".join(chunk),
                log=False,
            )

    def search(self, terms: Iterable[str], *, limit: int = 500) -> dict[str, set[str]]:
        """Return ``{doc_id: matched_terms}`` in one SQL round trip per call."""

        terms = tuple(dict.fromkeys(term for term in terms if term))
        if not terms:
            return {}
        matches: dict[str, set[str]] = {}
        for start in range(0, len(terms), 40):
            batch = terms[start : start + 40]
            rows = self._execute(
                "SELECT doc_id, term FROM doc_terms WHERE term IN ("
                + ",".join(_sql_literal(term) for term in batch)
                + f")"
            )
            for doc_id, term in rows:
                matches.setdefault(str(doc_id), set()).add(str(term))
        if not matches:
            return {}
        # A single term can match thousands of rows; keeping the map bounded
        # matters more than completeness for a context picker.
        if len(matches) > limit:
            ranked = sorted(matches.items(), key=lambda pair: -len(pair[1]))[:limit]
            matches = dict(ranked)
        return matches

    def document_frequency(self, terms: Iterable[str]) -> dict[str, int]:
        terms = tuple(dict.fromkeys(term for term in terms if term))
        if not terms:
            return {}
        rows = self._execute(
            "SELECT term, COUNT(*) FROM doc_terms WHERE term IN ("
            + ",".join(_sql_literal(term) for term in terms)
            + ") GROUP BY term"
        )
        return {str(term): int(count) for term, count in rows}

    def terms_with_prefix(self, prefixes: Iterable[str], *, limit: int = 60) -> list[str]:
        """Return index terms that extend one of ``prefixes``.

        One query for all prefixes keeps expansion from turning into a round
        trip per term.
        """

        usable = [prefix for prefix in dict.fromkeys(prefixes) if len(prefix) >= 4]
        if not usable:
            return []
        clause = " OR ".join(
            "term LIKE " + _sql_literal(prefix + "%") for prefix in usable[:20]
        )
        rows = self._execute(f"SELECT DISTINCT term FROM doc_terms WHERE {clause} LIMIT {int(limit)}")
        return [str(row[0]) for row in rows]

    def total_documents(self) -> int:
        rows = self._execute("SELECT COUNT(*) FROM docs", log=False)
        return int(rows[0][0]) if rows else 0

    def average_terms_per_document(self) -> float:
        """Average indexed-term count, used for BM25 length normalisation."""

        rows = self._execute("SELECT COUNT(*) FROM doc_terms", log=False)
        postings = int(rows[0][0]) if rows else 0
        documents = self.total_documents()
        if not documents:
            return 1.0
        return max(1.0, postings / documents)

    def fetch(self, doc_ids: Iterable[str]) -> dict[str, Doc]:
        ids = tuple(dict.fromkeys(doc_ids))
        if not ids:
            return {}
        rows = self._execute(
            "SELECT doc_id, kind, path, symbol, content, version, content_hash, "
            "line_start, line_end, metadata_json FROM docs WHERE doc_id IN ("
            + ",".join(_sql_literal(doc_id) for doc_id in ids)
            + ")"
        )
        docs: dict[str, Doc] = {}
        for row in rows:
            docs[str(row[0])] = Doc(
                doc_id=row[0],
                kind=row[1],
                path=row[2],
                symbol=row[3],
                content=row[4],
                version=row[5],
                content_hash=row[6],
                line_start=int(row[7]),
                line_end=int(row[8]),
                metadata=json.loads(row[9]),
            )
        return docs

    def query(self, terms: Iterable[str], *, kinds: Iterable[str] | None = None, limit: int = 20) -> list[Doc]:
        """Return documents matching any term, ordered by how many terms matched."""

        matches = self.search(terms)
        if not matches:
            return []
        docs = self.fetch(matches)
        kind_filter = set(kinds) if kinds is not None else None
        selected = [
            doc
            for doc_id, doc in docs.items()
            if kind_filter is None or doc.kind in kind_filter
        ]
        selected.sort(
            key=lambda doc: (-len(matches[doc.doc_id]), doc.kind, doc.path, doc.line_start)
        )
        return selected[:limit]

    def count(self) -> dict[str, int]:
        rows = self._execute("SELECT kind, COUNT(*) FROM docs GROUP BY kind", log=False)
        return {str(kind): int(count) for kind, count in rows}
