from uuid import uuid4

import pytest

from research_engine.domain.identity import (
    IdentityResolutionKind,
    IdentifierToken,
    decide_identity_resolution,
    normalize_identifier,
    normalize_identifiers,
    normalize_provider_identifier,
)
from research_engine.domain.value_objects import Identifier


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.1234/ABC.Def", "10.1234/abc.def"),
        (" DOI: 10.1234/ABC.Def ", "10.1234/abc.def"),
        ("https://doi.org/10.1234/ABC.Def", "10.1234/abc.def"),
        ("doi: https://doi.org/10.1234/ABC.Def", "10.1234/abc.def"),
        ("http://dx.doi.org/10.1234%2FABC.Def", "10.1234/abc.def"),
    ],
)
def test_doi_normalization_handles_prefixes_urls_and_case(value, expected):
    assert normalize_identifier("DOI", value) == IdentifierToken("doi", expected)


@pytest.mark.parametrize(
    ("scheme", "value", "expected"),
    [
        ("PMID", "001234", "001234"),
        ("pubmed_id", "PMID: 1234", "1234"),
        ("pmid", "https://pubmed.ncbi.nlm.nih.gov/1234/", "1234"),
        ("PMCID", "pmc12345", "PMC12345"),
        ("pmc_id", "PMCID: PMC12345", "PMC12345"),
        ("pmc", "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/", "PMC12345"),
    ],
)
def test_pubmed_identifiers_are_canonicalized(scheme, value, expected):
    token = normalize_identifier(scheme, value)
    expected_scheme = "pmid" if scheme.casefold() in {"pmid", "pubmed_id"} else "pmcid"
    assert token == IdentifierToken(expected_scheme, expected)


@pytest.mark.parametrize(
    ("scheme", "value"),
    [("doi", "not-a-doi"), ("pmid", "12A"), ("pmcid", "PMCx"), ("doi", " ")],
)
def test_invalid_known_identifiers_are_not_match_tokens(scheme, value):
    assert normalize_identifier(scheme, value) is None


def test_provider_identifiers_are_scoped_and_keep_provider_id_case():
    first = normalize_provider_identifier("Europe PMC", "MED:123")
    same_namespace = normalize_provider_identifier("europe pmc", "MED:123")
    other_namespace = normalize_provider_identifier("PubMed", "MED:123")

    assert first == IdentifierToken("provider:europe_pmc:id", "MED:123")
    assert same_namespace == first
    assert other_namespace != first
    assert normalize_identifier("provider_id", "MED:123") is None


def test_provider_specific_identifier_scheme_is_scoped_when_context_is_given():
    assert normalize_identifier("work", "W123", provider="OpenAlex") == IdentifierToken(
        "provider:openalex:work", "W123"
    )


def test_normalize_identifiers_deduplicates_to_stable_sql_keys():
    tokens = normalize_identifiers(
        [
            Identifier("doi", "https://doi.org/10.1234/ABC"),
            Identifier("DOI", "10.1234/abc"),
            Identifier("pmid", "123"),
        ]
    )

    assert tuple(token.key for token in tokens) == (
        ("doi", "10.1234/abc"),
        ("pmid", "123"),
    )


def test_identity_resolution_collapses_same_article_found_by_multiple_tokens():
    article_id = uuid4()

    result = decide_identity_resolution([article_id, article_id])

    assert result.kind is IdentityResolutionKind.MATCH
    assert result.article_id == article_id


def test_identity_resolution_distinguishes_new_match_and_conflict():
    first_article = uuid4()
    second_article = uuid4()

    new_result = decide_identity_resolution([])
    match_result = decide_identity_resolution([first_article])
    conflict_result = decide_identity_resolution([second_article, first_article])

    assert new_result.kind is IdentityResolutionKind.NEW
    assert new_result.article_id is None
    assert match_result.kind is IdentityResolutionKind.MATCH
    assert match_result.article_id == first_article
    assert conflict_result.kind is IdentityResolutionKind.CONFLICT
    assert conflict_result.article_id is None
    assert set(conflict_result.candidate_article_ids) == {first_article, second_article}
