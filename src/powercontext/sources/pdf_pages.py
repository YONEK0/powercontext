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
_STOP_WORDS = frozenset(
    """a about above after again against all am an and any are as at be because been before being
    below between both but by can cannot could did do does doing down during each few for from
    further had has have having he her here hers him himself his how i if in into is it its
    itself just may me might more most much must my no nor not now of off on once only or other
    ought our out over own same shall she should so some such than that the their them then there
    these they this those through to too under until up very was we were what when where which
    while who whom why will with would you your answer assume calculate calculated calculation
    define defined detail details directly explicitly give given investment analyst metrics
    perspective please question rely relying report reported reports respond shown state
    statement statements use used using fy""".split()
)

_QUERY_EXPANSIONS = {
    "geography": ("geographies", "region", "regions", "market", "markets", "segment", "segments", "country", "countries", "territory", "territories"),
    "geographies": ("geography", "region", "regions", "market", "markets", "segment", "segments", "country", "countries", "territory", "territories"),
    "geographic": ("geographies", "region", "regions", "market", "markets", "segment", "segments", "country", "countries", "territory", "territories"),
    "operate": ("operates", "operations", "operating", "business", "segment", "segments"),
    "operates": ("operate", "operations", "operating", "business", "segment", "segments"),
    "operations": ("operate", "operates", "operating", "business", "segment", "segments"),
    "legal": ("litigation", "claim", "claims", "proceeding", "proceedings", "investigation", "investigations", "lawsuit", "lawsuits"),
    "battle": ("battles", "litigation", "proceeding", "proceedings", "lawsuit", "lawsuits"),
    "battles": ("battle", "litigation", "proceeding", "proceedings", "lawsuit", "lawsuits"),
    "ebitda": ("operating", "profit", "income", "depreciation", "amortization"),
    "operating": ("profit", "profits", "income", "earnings"),
    "income": ("profit", "profits", "revenue", "revenues", "earnings"),
    "capex": ("capital", "expenditure", "expenditures", "property", "plant", "equipment"),
    "margin": ("revenue", "revenues", "sales", "percentage"),
    "unadjusted": ("operating", "income", "profit"),
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

    source_id = f"pdf:{document_id}:{document_sha256[:16]}:{page_number}"
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
    except (PdfReadError, ValueError, OSError, KeyError, TypeError, AttributeError) as error:
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
    try:
        extracted = page.extract_text()
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError):
        return ""
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


def _terms(text: str) -> list[str]:
    """Tokenize lowercased text into content terms for scoring."""

    return [token for token in _TOKEN_PATTERN.findall(text.lower()) if token not in _STOP_WORDS]


def _query_terms(question: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for token in _terms(question):
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms


def _expanded_query_terms(question_terms: list[str]) -> dict[str, float]:
    weights = {term: 1.0 for term in question_terms}
    for term in question_terms:
        for related in _QUERY_EXPANSIONS.get(term, ()):
            if related not in weights:
                weights[related] = 0.35
    return weights


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[Any, ...]:
    """Return distinct exact page citations ranked for one natural-language question."""

    from powercontext.builtin.artifacts.memory.models import MemoryCitation
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    if not isinstance(limit, int) or limit <= 0:
        return ()
    question_terms = _query_terms(question)
    if not question_terms:
        return ()

    # Memory FTS is conjunctive, so recall comes from original and expanded term candidates.
    weighted_terms = _expanded_query_terms(question_terms)
    candidates: dict[str, Any] = {}
    per_term = max(80, min(240, limit * 12))
    for term in weighted_terms:
        page = await memory.search(SearchMemoryRequest(query=term, mode="fts", limit=per_term))
        for hit in page.hits:
            candidates.setdefault(hit.entry_version_id, hit)
    if not candidates:
        return ()

    ranking = _bm25(weighted_terms, {key: hit.text for key, hit in candidates.items()})
    ranked_pages: list[tuple[MemoryCitation, tuple[str, int], str]] = []
    seen_pages: set[tuple[str, int]] = set()
    for key, _score in ranking:
        hit = candidates[key]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        try:
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        except Exception:  # noqa: BLE001 - a stale candidate must not break retrieval
            continue
        page_location = next((_page_location(source) for source in entry.sources if _page_location(source)), None)
        if page_location is None or page_location in seen_pages:
            continue
        seen_pages.add(page_location)
        ranked_pages.append((citation, page_location, hit.text))

    selected: list[tuple[MemoryCitation, tuple[str, int], str]] = []
    selected_locations: set[tuple[str, int]] = set()
    # Preserve evidence from every explicitly requested financial statement section.
    required_sections = _requested_sections(question)
    for section in required_sections:
        match = next(
            (item for item in ranked_pages if item[1] not in selected_locations and _page_section(item[2]) == section),
            None,
        )
        if match is not None:
            selected.append(match)
            selected_locations.add(match[1])
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
    heading = " | ".join(lines[:4]).lower()
    if "consolidated statement of income" in heading:
        return "income"
    if "consolidated statement of cash flows" in heading:
        return "cash-flow"
    if "legal proceedings" in heading:
        return "legal"
    return None


def _requested_sections(question: str) -> tuple[str, ...]:
    lowered = question.lower()
    sections: list[str] = []
    if any(term in lowered for term in ("income statement", "p&l", "operating income", "operating profit")):
        sections.append("income")
    if any(term in lowered for term in ("cash flow", "cash flows", "depreciation and amortization", "d&a")):
        sections.append("cash-flow")
    if any(term in lowered for term in ("legal battle", "legal proceeding", "litigation")):
        sections.append("legal")
    return tuple(dict.fromkeys(sections))


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
    return parts[0].removeprefix("pdf:"), page_number


def _bm25(terms: dict[str, float], documents: dict[str, str]) -> list[tuple[str, float]]:
    """Rank candidates with weighted Okapi BM25 and heading evidence."""

    tokenized = {key: _terms(text) for key, text in documents.items()}
    total = len(tokenized)
    if not total:
        return []
    query = tuple(terms)
    query_set = set(query)
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
        for term, query_weight in terms.items():
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            contribution = idf * (frequency * (k1 + 1)) / (
                frequency + k1 * (1 - b + b * length / average_length)
            )
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
