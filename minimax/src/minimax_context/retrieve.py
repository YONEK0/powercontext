"""Task-to-context selection over the seekdb index.

Nothing here knows about a specific ticket. Terms come from the task text plus
the corpus vocabulary, weights come from document frequency in seekdb, and the
target test / regression set is derived from the selected test documents. A new
task over a new project is a new index, not a code change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable

from .db import Doc, SeekDB, tokenize

# Function words only. Anything domain-specific is left to IDF weighting, which
# already suppresses words that appear everywhere in the corpus.
STOP_WORDS = frozenset(
    """
    a an and are as at be been but by can could do does for from had has have if
    in into is it its may might must no not of on or our shall should so such
    than that the their them then there these they this those to us was we were
    what when where which while who why will with without would you your
    """.split()
)

KIND_PRIOR = {"spec": 1.15, "code": 1.0, "test": 1.0}
# A normative delta *defines* the change; a process document only restates it.
# Without this, a long design.md out-scores the short rule it describes.
SPEC_ROLE_PRIOR = {"delta": 1.6, "canonical": 1.2, "process": 1.0}
# Relevance ranking alone lets several verbose process documents fill the spec
# slot and hide the delta that states the requirement. Each OpenSpec role gets a
# quota so a long proposal cannot crowd out the actual change definition.
SPEC_ROLE_QUOTA = {"delta": 2, "canonical": 1, "process": 2}
KIND_LIMITS = {"spec": 5, "code": 6, "test": 6}
MIN_SCORE = 0.35


@dataclass(frozen=True)
class ScoredDoc:
    doc: Doc
    score: float
    matched_terms: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.doc.doc_id,
            "kind": self.doc.kind,
            "path": self.doc.path,
            "symbol": self.doc.symbol,
            "line_start": self.doc.line_start,
            "line_end": self.doc.line_end,
            "version": self.doc.version,
            "content_hash": self.doc.content_hash,
            "score": round(self.score, 4),
            "matched_terms": list(self.matched_terms),
            "content": self.doc.content,
        }


@dataclass
class TaskPlan:
    task: str
    terms: list[str]
    groups: dict[str, list[ScoredDoc]] = field(default_factory=dict)
    acceptance_tests: list[str] = field(default_factory=list)
    regression_gate: list[str] = field(default_factory=list)

    @property
    def items(self) -> list[ScoredDoc]:
        return [item for bucket in self.groups.values() for item in bucket]

    @property
    def target_tests(self) -> list[str]:
        if self.acceptance_tests:
            return list(self.acceptance_tests)
        return [selector for selector, _ in self._test_ranking()[:1]]

    @property
    def regression_tests(self) -> list[str]:
        if self.acceptance_tests:
            return list(self.regression_gate)
        return [selector for selector, _ in self._test_ranking()[1:]]

    @property
    def candidate_tests(self) -> list[str]:
        return [selector for selector, _ in self._test_ranking()]

    def assign_tests(self, failed: list[str], passed: list[str]) -> None:
        """Use observed outcomes to label the acceptance test vs regression gate.

        Ranking alone cannot tell which test encodes the new behaviour; running
        them on the current code can. The first failing candidate in relevance
        order becomes the acceptance gate and the tests that already pass
        become the regression gate.
        """

        if not failed:
            return
        failed_set = set(failed)
        ordered = [selector for selector in self.candidate_tests if selector in failed_set]
        if not ordered:
            return
        accepted = set(ordered)
        self.acceptance_tests = ordered[:1]
        self.regression_gate = [
            selector
            for selector in self.candidate_tests
            if selector not in accepted and selector in set(passed)
        ]

    @property
    def target_files(self) -> list[str]:
        files: list[str] = []
        for item in self.groups.get("code", []):
            if item.doc.path not in files:
                files.append(item.doc.path)
        return files

    def _test_ranking(self) -> list[tuple[str, float]]:
        """Rank selected test functions by score; fall back to file-level docs.

        Ordering is by relevance to the task, so the top test is the one the
        change is expected to satisfy and the rest are the regression gate.
        """

        ranked: list[tuple[str, float]] = []
        for item in self.groups.get("test", []):
            if not item.doc.symbol or not item.doc.symbol.startswith("test"):
                continue
            selector = f"{item.doc.path}::{item.doc.symbol}"
            ranked.append((selector, item.score))
        ranked.sort(key=lambda pair: -pair[1])
        if ranked:
            return ranked
        return [
            (item.doc.path, item.score)
            for item in self.groups.get("test", [])
            if item.doc.symbol is None
        ]

    def as_dict(self) -> dict[str, object]:
        return {
            "task": self.task,
            "terms": self.terms,
            "groups": {
                kind: [item.as_dict() for item in bucket]
                for kind, bucket in self.groups.items()
            },
            "candidate_tests": self.candidate_tests,
            "target_tests": self.target_tests,
            "regression_tests": self.regression_tests,
            "target_files": self.target_files,
        }


def task_terms(task: str) -> list[str]:
    return [
        term
        for term in tokenize(task)
        if term not in STOP_WORDS and (len(term) >= 2)
    ]


def expand_terms(db: SeekDB, terms: Iterable[str]) -> list[str]:
    """Add corpus spellings of the same stem without a hand-written dictionary.

    `filter` finds `filtering`/`filtered` because those terms exist in the
    index; nothing is expanded that the project does not actually use.
    """

    expanded = list(dict.fromkeys(terms))
    for variant in db.terms_with_prefix(expanded):
        if variant not in expanded:
            expanded.append(variant)
    return expanded


SPEC_TERM_LIMIT = 400


def spec_bridge_terms(
    db: SeekDB, task_terms_: list[str], *, specs: int = 3, per_spec: int = 120
) -> list[str]:
    """Pull vocabulary out of the specs the task already matched.

    A task can be written in a different language from the code. The spec for
    the change is written for the same change, so it names the real fields and
    functions; re-querying with those names crosses the language gap without an
    embedding model or a translation table.
    """

    if not task_terms_:
        return []
    seed = _top_kinds(db, task_terms_, kinds=("spec",), limit=specs)
    if not seed:
        return []
    # Rare words carry the change's identity; common ones just re-match the
    # whole corpus.
    counter: dict[str, int] = {}
    for doc in seed:
        for term in doc.terms:
            if len(term) < 3 or term in STOP_WORDS:
                continue
            counter[term] = counter.get(term, 0) + 1
    frequencies = db.document_frequency(counter)
    ranked = sorted(
        counter,
        key=lambda term: (
            -counter[term],
            frequencies.get(term, 0),
            -len(term),
        ),
    )
    return ranked[:per_spec]


def _top_kinds(
    db: SeekDB, terms: list[str], *, kinds: tuple[str, ...], limit: int
) -> list[Doc]:
    matches = db.search(terms)
    if not matches:
        return []
    docs = db.fetch(matches)
    selected = [
        doc
        for doc_id, doc in docs.items()
        if doc.kind in kinds
    ]
    selected.sort(key=lambda doc: -len(matches[doc.doc_id]))
    return selected[:limit]


def _idf(db: SeekDB, terms: list[str]) -> dict[str, float]:
    total = max(1, db.total_documents())
    frequencies = db.document_frequency(terms)
    return {
        term: math.log(1.0 + (total - frequencies.get(term, 0) + 0.5) / (frequencies.get(term, 0) + 0.5))
        for term in terms
    }


# BM25 saturation constants. With tf fixed at 1 (a term is present or not) the
# only thing left from BM25 is length normalisation, which is exactly what keeps
# a long design document from outranking the short change spec that states the
# requirement.
K1 = 1.2
B = 0.75


def _length_factor(doc: Doc, average_terms: float) -> float:
    length = len(doc.terms)
    if length == 0:
        return 0.0
    normal = K1 * (1.0 - B + B * length / max(1.0, average_terms))
    return (K1 + 1.0) / (1.0 + normal)


def _field_bonus(doc: Doc, matched: Iterable[str]) -> float:
    path = doc.path.lower()
    symbol = (doc.symbol or "").lower()
    bonus = 0.0
    for term in matched:
        if term in symbol:
            bonus += 0.5
        if term in path:
            bonus += 0.25
    return bonus


def score_documents(
    db: SeekDB, terms: list[str], *, primary: Iterable[str] | None = None
) -> list[ScoredDoc]:
    """Rank documents, optionally marking which terms came from the task itself.

    ``primary`` terms (the task text) weigh more than bridged terms, so code
    reached through a spec does not outrank the spec that introduced it.
    """

    matches = db.search(terms)
    if not matches:
        return []
    idf = _idf(db, terms)
    average_terms = db.average_terms_per_document()
    primary_set = set(primary or ())
    docs = db.fetch(matches)
    scored: list[ScoredDoc] = []
    for doc_id, matched in matches.items():
        doc = docs.get(doc_id)
        if doc is None:
            continue
        length_factor = _length_factor(doc, average_terms)
        weight = 0.0
        for term in matched:
            importance = idf.get(term, 1.0)
            weight += importance * (length_factor if term in primary_set else length_factor * 0.6)
        weight *= KIND_PRIOR.get(doc.kind, 1.0)
        weight *= SPEC_ROLE_PRIOR.get(str(doc.metadata.get("role", "")), 1.0)
        weight += _field_bonus(doc, matched & primary_set if primary_set else matched)
        if weight < MIN_SCORE:
            continue
        scored.append(
            ScoredDoc(
                doc=doc,
                score=weight,
                matched_terms=tuple(sorted(matched)),
            )
        )
    scored.sort(
        key=lambda item: (-item.score, item.doc.kind, item.doc.path, item.doc.line_start)
    )
    return scored


def _dedupe_overlapping(scored: list[ScoredDoc]) -> list[ScoredDoc]:
    """Drop whole-file documents when a symbol from that file is already selected.

    Both are useful in the evidence, so the file is kept only when it carries
    text the symbol does not (for example the imports and data loading).
    """

    symbol_paths = {
        (item.doc.path, item.doc.symbol)
        for item in scored
        if item.doc.symbol
    }
    symbol_files = {path for path, _ in symbol_paths}
    seen: set[str] = set()
    result: list[ScoredDoc] = []
    for item in scored:
        if item.doc.doc_id in seen:
            continue
        if (
            item.doc.symbol is None
            and item.doc.path in symbol_files
            and len(item.doc.content) < 400
        ):
            continue
        seen.add(item.doc.doc_id)
        result.append(item)
    return result


def _spec_quota_ok(bucket: list[ScoredDoc], candidate: ScoredDoc) -> bool:
    role = str(candidate.doc.metadata.get("role", ""))
    if not role:
        return True
    used = sum(1 for item in bucket if str(item.doc.metadata.get("role", "")) == role)
    return used < SPEC_ROLE_QUOTA.get(role, 2)


def build_plan(db: SeekDB, task: str) -> TaskPlan:
    base = task_terms(task)
    terms = expand_terms(db, base)
    bridged = [
        term for term in spec_bridge_terms(db, base) if term not in set(terms)
    ]
    terms = terms + bridged
    scored = _dedupe_overlapping(score_documents(db, terms, primary=base))

    groups: dict[str, list[ScoredDoc]] = {"spec": [], "code": [], "test": []}
    for item in scored:
        bucket = groups.setdefault(item.doc.kind, [])
        if len(bucket) >= KIND_LIMITS.get(item.doc.kind, 4):
            continue
        if item.doc.kind == "spec" and not _spec_quota_ok(bucket, item):
            continue
        bucket.append(item)

    return TaskPlan(task=task, terms=terms, groups=groups)


def select_context(db: SeekDB, task: str, *, limit: int = 12) -> dict[str, list[ScoredDoc]]:
    """Backwards-compatible view returning only the grouped sources."""

    plan = build_plan(db, task)
    return plan.groups


def test_selectors(doc: Doc) -> list[str]:
    """Return pytest selectors described by a test document."""

    if doc.symbol and doc.symbol.startswith("test"):
        return [f"{doc.path}::{doc.symbol}"]
    names = re.findall(r"^def (test_[A-Za-z0-9_]+)", doc.content, flags=re.MULTILINE)
    return [f"{doc.path}::{name}" for name in names]
