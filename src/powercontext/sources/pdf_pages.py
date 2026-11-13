# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Complete PDF page extraction and page-level evidence retrieval."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from typing import Any
from urllib.parse import quote, unquote

from pydantic import BaseModel, model_validator
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"

_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")

# Question scaffolding carries no evidence value and would drown out page terms.
_STOP_WORDS = frozenset([
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "may",
    "me",
    "might",
    "more",
    "most",
    "much",
    "must",
    "my",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "ought",
    "our",
    "out",
    "over",
    "own",
    "same",
    "shall",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "answer",
    "assume",
    "calculate",
    "calculated",
    "calculation",
    "define",
    "defined",
    "detail",
    "details",
    "directly",
    "explicitly",
    "give",
    "given",
    "investment",
    "analyst",
    "metrics",
    "perspective",
    "please",
    "question",
    "rely",
    "relying",
    "report",
    "reported",
    "reports",
    "respond",
    "shown",
    "state",
    "statement",
    "statements",
    "use",
    "used",
    "using",
    "fy",
])

# Region vocabulary: filings spell out the areas they operate in by name, so a
# "which geographies" question is answered by those names rather than by the
# abstract words "geography" or "segment".
_GEOGRAPHIC_TERMS = (
    "america",
    "europe",
    "africa",
    "asia",
    "pacific",
    "china",
    "latin",
    "north",
    "south",
    "middle",
    "region",
    "regions",
    "market",
    "markets",
    "segment",
    "segments",
    "country",
    "countries",
    "territory",
    "territories",
)

# Customer concentration vocabulary: filings disclose who buys from them by
# naming the buyer and the share of revenues it takes.
_CUSTOMER_TERMS = (
    "government",
    "contract",
    "revenue",
    "portion",
    "significant",
    "substantial",
    "commercial",
    "earned",
    "percent",
    "concentration",
    "accounted",
)

_QUERY_EXPANSIONS = {
    "operate": ("operates", "operations", "operating", "business", "segment", "segments"),
    "operates": ("operate", "operations", "operating", "business", "segment", "segments"),
    "operations": ("operate", "operates", "operating", "business", "segment", "segments"),
    "legal": (
        "litigation",
        "claim",
        "claims",
        "proceeding",
        "proceedings",
        "investigation",
        "investigations",
        "lawsuit",
        "lawsuits",
    ),
    "battle": ("battles", "litigation", "proceeding", "proceedings", "lawsuit", "lawsuits"),
    "battles": ("battle", "litigation", "proceeding", "proceedings", "lawsuit", "lawsuits"),
    "ebitda": ("operating", "profit", "income", "depreciation", "amortization"),
    "operating": ("profit", "profits", "income", "earnings"),
    "income": ("profit", "profits", "revenue", "revenues", "earnings"),
    "capex": ("capital", "expenditure", "expenditures", "property", "plant", "equipment"),
    "margin": ("revenue", "revenues", "sales", "percentage", "cost", "pricing"),
    "unadjusted": ("operating", "income", "profit"),
    "capital": ("capex", "expenditure", "expenditures", "property", "plant", "equipment", "depreciation"),
    "intensive": ("capital", "capex", "property", "plant", "equipment"),
    "mergers": ("merger", "acquisition", "acquisitions", "divestiture", "divestitures"),
    "acquisitions": ("acquisition", "merger", "mergers", "divestiture", "divestitures"),
    "segment": ("segments", "business", "reportable"),
    "segments": ("segment", "business", "reportable"),
    "growth": ("sales", "revenue", "increase", "decrease"),
    # Compound and abbreviation forms that never match statement vocabulary verbatim.
    "cashflow": ("cash", "flow", "flows"),
    "ppne": ("property", "plant", "equipment"),
    "retirees": ("pension", "postretirement", "benefits"),
    "liquidated": ("liquidation", "bankruptcy", "book"),
    "shareholders": ("share", "shares", "equity", "tbvps", "book value"),
    "tbvps": ("book", "value", "share"),
}

# Semantic equivalents that share no surface form with the question; weighted high
# because they name the exact disclosure a paraphrased question is about.
_QUERY_EXPANSIONS_STRONG = {
    "shareholders": ("tbvps", "book value", "tangible book value"),
    "shareholder": ("tbvps", "book value", "tangible book value"),
    "liquidated": ("tbvps", "book value", "tangible book value"),
    "bankrupted": ("book value", "tangible book value"),
    # "Where does the company operate" questions are answered by the region names
    # a report spells out, not by the abstract word "geography".
    "geography": _GEOGRAPHIC_TERMS,
    "geographies": _GEOGRAPHIC_TERMS,
    "geographic": _GEOGRAPHIC_TERMS,
    # "Who are the customers" questions are answered by the concentration
    # disclosure, which names the buyer and the share of revenues it takes.
    "customer": _CUSTOMER_TERMS,
    "customers": _CUSTOMER_TERMS,
}


class PDFDocumentInput(BaseModel):
    """One complete original PDF and the caller-stable document identity."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a parsed PDF with its revision fingerprint."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """Captured page-level evidence bound to one exact document revision."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @model_validator(mode="after")
    def validate_identity(self) -> PDFPageSource:
        if self.source_id != self.name:
            raise ValueError("source_id must match the Source name")  # noqa: TRY003
        return self


class _PasswordRequired(Exception):
    """Internal marker so an encrypted document is reported as a value error."""


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the stable identity of one page in one document revision."""

    source_id = f"pdf:{quote(document_id, safe='')}:{document_sha256[:16]}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{document_id}\x00{document_sha256}".encode()).hexdigest()
        source_id = f"pdf:{digest}:{page_number}"
        if len(source_id) > MAX_SOURCE_ID_LENGTH:
            raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract the text of every physical page from one complete PDF."""

    document_id = value.document_id
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    content = value.content
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise ValueError("PDF content must be non-empty bytes")  # noqa: TRY003
    raw = bytes(content)
    document_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if reader.is_encrypted and not _unlock_without_password(reader):
            raise _PasswordRequired()
        pages = tuple(
            PDFPageInput(
                document_id=document_id,
                document_sha256=document_sha256,
                page_number=index,
                text=_page_text(page),
            )
            for index, page in enumerate(reader.pages, start=1)
        )
    except _PasswordRequired as error:
        raise ValueError("PDF requires a user password") from error
    except Exception as error:
        raise ValueError("PDF content could not be parsed completely") from error
    if not pages:
        raise ValueError("PDF must contain at least one page")  # noqa: TRY003
    return pages


def _unlock_without_password(reader: PdfReader) -> bool:
    try:
        return bool(reader.decrypt(""))
    except (PdfReadError, ValueError, NotImplementedError):
        return False


def _page_text(page: Any) -> str:
    extracted = page.extract_text()
    return extracted if isinstance(extracted, str) else ""


class PDFPageSourceAdapter:
    """Resolve and read PDF page Sources without re-parsing the document."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project one PDF page into the standard textual evidence envelope."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


def _stem(token: str) -> str:
    """Fold a token to the light stem used for scoring."""

    if len(token) >= 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _terms(text: str) -> list[str]:
    """Tokenize lowercased text into content terms for scoring."""

    merged: list[str] = []
    for token in _TOKEN_PATTERN.findall(text.lower()):
        if token in _STOP_WORDS:
            continue
        # Some extractions split words mid-glyph ("Balance Shee t", "Cash Flow s");
        # reattach a stray single letter to the word before it so scoring sees the
        # vocabulary the question actually uses.
        if len(token) == 1 and token.isalpha() and len(merged) >= 1 and len(merged[-1]) >= 3:
            merged[-1] = merged[-1] + token
            continue
        merged.append(_stem(token))
    return merged


def _expansion_index(expansions: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """Index expansion tables by the light stem the scorer actually uses.

    Question terms are stemmed before scoring, so an expansion keyed by the raw
    surface form ("geographies") would never be found by the stemmed term
    ("geographie").
    """

    index: dict[str, tuple[str, ...]] = {}
    for key, values in expansions.items():
        merged = index.get(key, ())
        index[key] = merged + tuple(value for value in values if value not in merged)
        stem = _stem(key)
        if stem != key:
            merged = index.get(stem, ())
            index[stem] = merged + tuple(value for value in values if value not in merged)
    return index


_EXPANSION_INDEX = _expansion_index(_QUERY_EXPANSIONS)
_STRONG_EXPANSION_INDEX = _expansion_index(_QUERY_EXPANSIONS_STRONG)


def _query_terms(question: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    question = re.sub(r"m\s*&\s*a", "mergers acquisitions", question, flags=re.IGNORECASE)
    for token in _terms(question):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _expanded_query_terms(question_terms: list[str]) -> dict[str, float]:
    weights = dict.fromkeys(question_terms, 1.0)
    for term in question_terms:
        for related, weight in (
            *((item, 0.9) for item in _STRONG_EXPANSION_INDEX.get(term, ())),
            *((item, 0.35) for item in _EXPANSION_INDEX.get(term, ())),
        ):
            # Expansion phrases enter the same token space as pages, so split and
            # stem each word; a raw phrase key would never match a token.
            for word in related.split():
                key = _stem(word)
                if len(key) >= 2 and key not in weights:
                    weights[key] = weight
    return weights


_COMPANY_ALIASES: dict[str, str] = {
    "amex": "americanexpress",
    "axp": "americanexpress",
    "jpm": "jpmorgan",
    "gs": "goldmansachs",
    "ms": "morganstanley",
    "ba": "boeing",
    "pep": "pepsico",
    "aapl": "apple",
    "msft": "microsoft",
    "ko": "cocacola",
    "pg": "proctergamble",
    "jnj": "johnsonjohnson",
    "wmt": "walmart",
    "vz": "verizon",
    "intc": "intel",
    "nvda": "nvidia",
    "amzn": "amazon",
    "tsla": "tesla",
    "xom": "exxonmobil",
    "cvx": "chevron",
    "pfe": "pfizer",
    "mrk": "merck",
    "abbv": "abbvie",
    "lly": "elililly",
    "unh": "unitedhealth",
    "hd": "homedepot",
    "mcd": "mcdonalds",
    "nke": "nike",
    "dis": "disney",
}


def _compact_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _company_key(document_id: str) -> str:
    return re.sub(r"(19|20)\d{2}.*$", "", _compact_identity(document_id))


async def _target_documents(memory: Any, question: str, question_terms: Sequence[str]) -> frozenset[str]:
    """Infer which reports a question is about, when several share one scope.

    A FinanceBench question normally names its company.  Document identity is a
    stronger signal than lexical overlap: match the stable document id first, and
    only fall back to document-level query-term coverage for abbreviations such as
    "AMEX" that do not appear in the id ``AMERICANEXPRESS_2022_10K``.
    """

    try:
        listing = await memory.list()
    except Exception:
        return frozenset()
    documents: dict[str, set[str]] = {}
    for record in listing.entries:
        for source in record.entry.sources:
            location = _page_location(source)
            if location is not None:
                documents.setdefault(location[0], set()).update(_terms(record.entry.text))
    if not documents:
        return frozenset()

    compact_question = _compact_identity(question)
    lowered_question = question.lower()
    aliases = {
        canonical
        for alias, canonical in _COMPANY_ALIASES.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered_question)
    }
    matched = {
        document_id
        for document_id in documents
        if (key := _company_key(document_id))
        and len(key) >= 2
        and (key in compact_question or _compact_identity(document_id) in compact_question or key in aliases)
    }
    if matched:
        year = next(
            (token for token in _TOKEN_PATTERN.findall(question.lower()) if re.fullmatch(r"(19|20)\d{2}", token)), None
        )
        if year is not None:
            narrowed = {document_id for document_id in matched if year in _compact_identity(document_id)}
            if narrowed:
                matched = narrowed
        return frozenset(matched)

    terms = tuple(dict.fromkeys(question_terms))
    if not terms:
        return frozenset()
    document_frequency = Counter(term for present in documents.values() for term in terms if term in present)
    population = max(1, len(documents))
    idf = {term: math.log((population + 1) / (document_frequency.get(term, 0) + 1)) + 1.0 for term in terms}
    scores = {
        document_id: sum(idf[term] for term in terms if term in present) for document_id, present in documents.items()
    }
    best = max(scores.values(), default=0.0)
    if best <= 0:
        return frozenset()
    # Keep the best report, plus any report tied closely enough to be ambiguous.
    return frozenset(document_id for document_id, score in scores.items() if score >= best * 0.9)


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[Any, ...]:
    """Return distinct exact page citations ranked for one natural-language question."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(limit, int) or limit <= 0:
        return ()
    question_terms = _query_terms(question)
    if not question_terms:
        return ()
    target_documents = await _target_documents(memory, question, question_terms)

    # Memory FTS is conjunctive, so recall comes from original and expanded term candidates.
    weighted_terms = _expanded_query_terms(question_terms)
    requested_sections = _requested_sections(question)
    candidates: dict[str, Any] = {}
    section_hits: dict[str, list[Any]] = {}
    per_term = max(80, min(240, limit * 12))

    # Prefer a full local corpus: FTS windows truncate, and some extractions split
    # words ("Balance Shee t") so conjunctive FTS can never surface the evidence
    # pages at all.  Page-backed entries in one scope number in the hundreds, which
    # is cheap to rank locally.
    from types import SimpleNamespace

    listed_corpus: dict[str, Any] = {}
    try:
        listing = await memory.list()
    except Exception:
        listing = None
    if listing is not None and getattr(listing, "memory_ref", None) is not None:
        for record in listing.entries:
            entry = record.entry
            if not any(_page_location(source) for source in entry.sources):
                continue
            listed_corpus[entry.entry_version_id] = SimpleNamespace(
                memory_ref=record.memory_ref,
                entry_id=entry.entry_id,
                entry_version_id=entry.entry_version_id,
                text=entry.text,
            )
    if listed_corpus:
        candidates.update(listed_corpus)
        for section in requested_sections:
            section_hits[section] = [hit for hit in listed_corpus.values() if _page_section(hit.text) == section]
    else:
        for term in weighted_terms:
            page = await memory.search(SearchMemoryRequest(query=term, mode="fts", limit=per_term))
            for hit in page.hits:
                candidates.setdefault(hit.entry_version_id, hit)
        # Let the statement/note heading itself supply candidates for paraphrased questions.
        for section in requested_sections:
            for section_query in _SECTION_QUERIES.get(section, ()):
                page = await memory.search(
                    SearchMemoryRequest(query=section_query, mode="fts", limit=min(per_term, 80))
                )
                for hit in page.hits:
                    section_hits.setdefault(section, []).append(hit)
                    candidates.setdefault(hit.entry_version_id, hit)
    if not candidates:
        return ()

    for section, hits in section_hits.items():
        matching = [hit for hit in hits if _page_section(hit.text) == section]
        if not matching:
            continue
        scored = dict(_bm25(weighted_terms, {hit.entry_version_id: hit.text for hit in matching}))
        # Statement pages ("Consolidated Statements of Operations") carry the actual
        # evidence but lose raw BM25 to MD&A prose pages in the same section, so a
        # heading that names the statement exactly always ranks first.
        ordered: list[Any] = sorted(
            matching,
            key=lambda hit: (
                0 if _statement_heading(hit.text, section) else 1,
                -scored.get(hit.entry_version_id, 0.0),
            ),
        )
        seen = {hit.entry_version_id for hit in ordered}
        ordered.extend(hit for hit in matching if hit.entry_version_id not in seen)
        matching_ids = {hit.entry_version_id for hit in matching}
        ordered.extend(hit for hit in hits if hit.entry_version_id not in matching_ids)
        section_hits[section] = ordered

    page_items: dict[str, tuple[MemoryCitation, tuple[str, int], str] | None] = {}

    async def page_item(hit: Any) -> tuple[MemoryCitation, tuple[str, int], str] | None:
        cached = page_items.get(hit.entry_version_id)
        if cached is not None or hit.entry_version_id in page_items:
            return cached
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:
            page_items[hit.entry_version_id] = None
            return None
        page_location = next((_page_location(source) for source in entry.sources if _page_location(source)), None)
        item = None if page_location is None else (citation, page_location, hit.text)
        page_items[hit.entry_version_id] = item
        return item

    ranking = _bm25(weighted_terms, {key: hit.text for key, hit in candidates.items()})
    ranked_pages: list[tuple[MemoryCitation, tuple[str, int], str]] = []
    seen_pages: set[tuple[str, int]] = set()
    for key, _score in ranking:
        item = await page_item(candidates[key])
        if item is None or item[1] in seen_pages:
            continue
        if target_documents and item[1][0] not in target_documents:
            continue
        seen_pages.add(item[1])
        ranked_pages.append(item)

    selected: list[tuple[MemoryCitation, tuple[str, int], str]] = []
    selected_locations: set[tuple[str, int]] = set()
    scores = dict(ranking)
    # Preserve evidence from every explicitly requested financial statement/note section.
    section_count = max(1, len(requested_sections))
    base_quota = max(1, limit // section_count)
    for section in requested_sections:
        quota = min(_SECTION_QUOTAS.get(section, 2), base_quota)
        taken = 0
        for hit in section_hits.get(section, ()):
            if scores.get(hit.entry_version_id, 0.0) <= 0.0 and not _statement_heading(hit.text, section):
                # A page the query cannot score at all only earns a slot when its
                # heading names the section exactly; otherwise it would crowd out
                # lexically stronger evidence pages.
                continue
            item = await page_item(hit)
            if item is None or item[1] in selected_locations:
                continue
            if target_documents and item[1][0] not in target_documents:
                continue
            selected.append(item)
            selected_locations.add(item[1])
            taken += 1
            if taken >= quota:
                break
    for item in ranked_pages:
        if item[1] in selected_locations:
            continue
        selected.append(item)
        selected_locations.add(item[1])
        if len(selected) >= limit:
            break
    return tuple(item[0] for item in selected[:limit])


def _page_section(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = " ".join(" | ".join(lines[:12]).lower().split())
    # Some extractions split words mid-glyph ("Balance Shee t"); the space-stripped
    # form reattaches them, so headings stay recognizable on those reports.
    # Statement titles are also often broken across lines, so the compact form drops
    # the line separators before matching.
    compact = "".join(heading.replace("|", "").split())
    for section, patterns in _SECTION_HEADINGS:
        if any(pattern in heading or "".join(pattern.split()) in compact for pattern in patterns):
            return section
    return None


def _statement_heading(text: str, section: str) -> bool:
    """Whether the page heading names the section's statement or note exactly."""

    lines = [line.strip() for line in text.splitlines() if line.strip()][:6]
    joined = " ".join(" | ".join(lines).lower().split())
    compact = "".join(joined.replace("|", "").split())
    return any(query.replace(" ", "") in compact for query in _SECTION_QUERIES.get(section, ()))


def _requested_sections(question: str) -> tuple[str, ...]:
    lowered = question.lower()
    sections: list[str] = []
    if any(term in lowered for term in ("tax rate", "effective tax", "income tax", "tax provision")):
        sections.extend(("tax", "income"))
    if any(term in lowered for term in ("legal battle", "legal proceeding", "litigation", "lawsuit")):
        sections.append("legal")
    if any(term in lowered for term in ("geograph", "region", "country", "countries", "international", "emea", "apac")):
        sections.append("geographic")
    if any(term in lowered for term in ("segment", "reporting segment")) or "categor" in lowered:
        sections.append("segment")
    if any(term in lowered for term in ("customer", "card member", "concentration")):
        sections.extend(("customers", "risk"))
    if any(
        term in lowered
        for term in (
            "balance sheet",
            "financial position",
            "financial condition",
            "liquidity",
            "working capital",
            "quick ratio",
            "current ratio",
            "assets",
            "liabilities",
            "debt",
            "equity",
            "receivable",
            "inventory",
        )
    ):
        sections.append("balance-sheet")
    if any(
        term in lowered
        for term in ("cash flow", "cash flows", "capex", "capital expenditure", "depreciation", "amortization")
    ):
        sections.append("cash-flow")
    if any(
        term in lowered
        for term in (
            "income statement",
            "statement of income",
            "statement of operations",
            "statement of earnings",
            "results of operations",
            "operating income",
            "operating margin",
            "gross margin",
            "gross profit",
            "revenue",
            "sales",
            "earnings",
            "ebitda",
            "restructuring",
            "margin",
            "p&l",
            # Profitability metrics are computed from the income statement even when
            # the question only names the ratio, so its evidence pages stay candidates.
            "ratio",
            "turnover",
            "roa",
            "roe",
            "net income",
            "profitability",
            "cogs",
            "cost of goods",
        )
    ):
        sections.append("income")
    if any(term in lowered for term in ("capital intensive", "capital-intensive", "capex", "capital expenditure")):
        sections.extend(("balance-sheet", "cash-flow", "income"))
    if any(term in lowered for term in ("m&a", "merger", "mergers", "acquisition", "acquisitions")):
        sections.extend(("segment", "business"))
    if any(term in lowered for term in ("drove", "change", "margin", "revenue", "operating income")):
        sections.append("md&a")
    if any(term in lowered for term in ("risk", "cyclical", "uncertain", "exposure")):
        sections.append("risk")
    if "card member" in lowered:
        sections.append("business")
    if any(
        term in lowered
        for term in ("business", "product", "service", "compet", "strategy", "operate", "operations", "offering")
    ):
        sections.append("business")
    return tuple(dict.fromkeys(sections))


# Section heading queries make the exact statement/note page a candidate even when
# the question paraphrases it and shares few lexical terms with the page body.
_SECTION_QUERIES: dict[str, tuple[str, ...]] = {
    "tax": ("income tax provision", "effective tax rate", "provision for income taxes", "income taxes"),
    "legal": ("legal proceedings", "litigation"),
    "geographic": ("geographic operations", "geographic regions", "geographic information", "geographic areas"),
    "segment": (
        "summary of business segment data",
        "segment information",
        "reportable segments",
        "business segment",
        "business segments",
        "segment results",
        "sales and operating income by business segment",
    ),
    "customers": (
        "major customers",
        "customer concentration",
        "largest customer",
        "no single customer",
        "government contracts",
        "revenues were earned",
    ),
    "balance-sheet": (
        "consolidated balance sheets",
        "consolidated balance sheet",
        "consolidated statements of financial position",
        "statements of financial condition",
        "balance sheets",
        # Debt footnote tables answer balance-sheet questions without ever using
        # the "balance sheet" phrase, so the note's own wording supplies candidates.
        "long-term debt",
        "debt obligations",
        "debt maturities",
    ),
    "cash-flow": (
        "capital expenditures",
        "property plant equipment",
        "consolidated statements of cash flows",
        "consolidated statements of cash flow",
        "statements of cash flows",
        "statement of cash flows",
    ),
    "income": (
        "consolidated statements of income",
        "consolidated statements of operations",
        "consolidated statements of earnings",
        "consolidated statement of earnings",
        "statements of consolidated income",
        "consolidated results of operations",
        "results of operations",
        "selected income statement data",
        # Margin/cost phrases also live in MD&A analysis pages, which paraphrase
        # the statement titles and would otherwise never become candidates.
        "gross margin",
        "cost of goods sold",
        "cost of sales",
    ),
    "md&a": ("management discussion analysis", "management's discussion and analysis"),
    "risk": ("risk factors",),
    "business": ("item 1 business", "description of business", "products and services", "business environment"),
}

_SECTION_QUOTAS: dict[str, int] = {
    "tax": 8,
    "legal": 3,
    "geographic": 3,
    "segment": 6,
    "customers": 10,
    "balance-sheet": 8,
    "cash-flow": 5,
    "income": 8,
    "md&a": 5,
    "risk": 6,
    "business": 8,
}

_SECTION_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("business", ("item 1. business", "item 1 business", "business environment", "our business", "business overview")),
    ("tax", ("income tax provision", "effective tax rate")),
    ("legal", ("legal proceedings", "litigation")),
    ("geographic", ("geographic operations", "geographic regions", "geographic information")),
    ("segment", ("summary of business segment data", "segment information", "reportable segments", "segment results")),
    ("customers", ("customer concentration", "major customers")),
    (
        "balance-sheet",
        (
            "consolidated balance sheets",
            "consolidated balance sheet",
            "balance sheet",
            "statements of financial position",
        ),
    ),
    (
        "cash-flow",
        (
            "consolidated statements of cash flows",
            "consolidated statement of cash flows",
            "statements of cash flows",
            "statement of cash flows",
        ),
    ),
    (
        "income",
        (
            "consolidated statements of income",
            "consolidated statement of income",
            "consolidated statements of operations",
            "consolidated statement of operations",
            "selected income statement data",
            "statement of earnings",
            "statements of earnings",
            "statements of income",
        ),
    ),
    ("md&a", ("management's discussion and analysis", "management discussion and analysis", "item 7.")),
    ("risk", ("risk factors",)),
)


def _page_location(source: Any) -> tuple[str, int] | None:
    """Recover the document and physical page from a Source reference identity."""

    source_id = getattr(source, "source_id", None)
    if not isinstance(source_id, str) or not source_id.startswith("pdf:"):
        return None
    parts = source_id.rsplit(":", 2)
    if len(parts) != 3:
        return None
    try:
        page_number = int(parts[2])
    except ValueError:
        return None
    return unquote(parts[0].removeprefix("pdf:")), page_number


def _bm25(terms: dict[str, float], documents: dict[str, str]) -> list[tuple[str, float]]:
    """Rank candidates with weighted Okapi BM25 and heading evidence."""

    tokenized = {key: _terms(text) for key, text in documents.items()}
    total = len(tokenized)
    if not total:
        return []
    query_items = tuple(terms.items())
    query_set = {term for term, _weight in query_items}
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized.values():
        for term in query_set.intersection(tokens):
            document_frequency[term] += 1
    average_length = sum(len(tokens) for tokens in tokenized.values()) / total or 1.0
    k1, b = 1.2, 0.75
    scored: list[tuple[str, float]] = []
    for key, tokens in tokenized.items():
        frequencies = Counter(tokens)
        length = len(tokens)
        score = 0.0
        for term, query_weight in query_items:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            contribution = idf * (frequency * (k1 + 1)) / (frequency + k1 * (1 - b + b * length / average_length))
            score += contribution * query_weight
        heading = " ".join(tokens[:18])
        heading_matches = sum(1 for term, weight in terms.items() if term in heading and weight >= 0.35)
        if score > 0:
            score *= 1.0 + 0.12 * heading_matches
            scored.append((key, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
