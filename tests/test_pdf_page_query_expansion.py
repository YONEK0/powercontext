"""Regression tests for the PDF evidence-page query expansions."""

import pytest

pytest.importorskip("pypdf")

from powercontext.sources.pdf_pages import _expanded_query_terms, _query_terms


def test_plural_question_term_reaches_its_expansion_table() -> None:
    """A stemmed question term still resolves the expansion keyed by its surface form."""

    weights = _expanded_query_terms(_query_terms("What are the geographies that Pepsico primarily operates in?"))

    assert "region" in weights
    assert "america" in weights


def test_customer_question_expands_to_concentration_vocabulary() -> None:
    weights = _expanded_query_terms(_query_terms("Who are the primary customers of Boeing as of FY2022?"))

    assert weights["government"] > 0.0
    assert weights["revenue"] > 0.0
    assert weights["portion"] > 0.0
