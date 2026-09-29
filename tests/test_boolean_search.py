"""Focused coverage for the portable Boolean search grammar and adapters."""

import base64
import json

import httpx
import pytest

from research_engine.domain.search_expression import (
    SearchExpressionError,
    candidate_terms,
    evaluate_expression,
    matches_metadata,
    parse_expression,
    render_expression,
)
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.query_preview import QueryPreviewService
from research_engine.infrastructure.providers.crossref import CrossrefProvider
from research_engine.infrastructure.providers.europe_pmc import EuropePMCProvider
from research_engine.infrastructure.providers.http import ProviderResponseError
from research_engine.infrastructure.providers.openalex import OpenAlexProvider
from research_engine.infrastructure.providers.pubmed import PubMedProvider
from research_engine.ports.providers import SearchRequest


def _response(request: httpx.Request, items, *, next_cursor=None):
    payload = {
        "message": {
            "total-results": 0 if not items else len(items),
            "next-cursor": next_cursor,
            "items": items,
        }
    }
    body = json.dumps(payload).encode()
    return httpx.Response(
        200,
        request=request,
        headers={"content-type": "application/json; charset=utf-8"},
        content=body,
    )


def _work(doi: str, title: str, *, abstract: str = "", subjects=()):
    return {
        "DOI": doi,
        "title": [title],
        "abstract": abstract,
        "subject": list(subjects),
    }


def test_parser_applies_not_then_and_then_or_precedence_and_grouping():
    expression = parse_expression("alpha OR beta AND NOT gamma")

    assert evaluate_expression(expression, lambda term: term.text == "alpha") is True
    assert evaluate_expression(expression, lambda term: term.text in {"beta", "gamma"}) is False
    assert evaluate_expression(expression, lambda term: term.text in {"beta"}) is True
    assert evaluate_expression(
        parse_expression("(alpha OR beta) AND NOT gamma"),
        lambda term: term.text in {"alpha", "gamma"},
    ) is False
    assert evaluate_expression(
        parse_expression("NOT (alpha OR beta)"), lambda term: False
    ) is True
    double_not = parse_expression("NOT NOT alpha")
    assert evaluate_expression(double_not, lambda term: True) is True
    assert evaluate_expression(double_not, lambda term: False) is False
    assert render_expression(double_not) == "alpha"


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "AND alpha",
        "alpha OR",
        "alpha AND OR beta",
        "(alpha OR beta",
        "alpha)",
        '"unterminated',
        '"   "',
        "alpha[tiab]",
        "alpha*",
        "alpha | beta",
    ],
)
def test_parser_rejects_invalid_and_nonportable_syntax(expression):
    with pytest.raises(SearchExpressionError):
        parse_expression(expression)


def test_parser_enforces_expression_token_depth_and_character_limits():
    with pytest.raises(SearchExpressionError, match="5,000 characters"):
        parse_expression("a" * 5001)
    with pytest.raises(SearchExpressionError, match="256 search tokens"):
        parse_expression(" OR ".join(["alpha"] * 129))
    with pytest.raises(SearchExpressionError, match="32 levels"):
        parse_expression("(" * 33 + "alpha" + ")" * 33)
    with pytest.raises(SearchExpressionError, match="32 levels"):
        parse_expression("NOT " * 33 + "alpha")


def test_quoted_phrases_require_adjacency_in_a_single_field_but_words_do_not():
    fields = ("Lung", "cancer outcomes")
    assert matches_metadata(parse_expression('"lung cancer"'), fields) is False
    assert matches_metadata(parse_expression("lung cancer"), fields) is True
    assert matches_metadata(
        parse_expression('"lung cancer"'), ("LUNG cancer outcomes",)
    ) is True


def test_not_binds_to_one_word_before_adjacent_word_and_and():
    expression = parse_expression("NOT alpha beta")

    # Adjacency is AND and NOT has higher precedence, so alpha must be absent
    # while beta is present. An alpha-only paper must not match.
    assert matches_metadata(expression, ("alpha only",)) is False
    assert matches_metadata(expression, ("beta only",)) is True
    assert candidate_terms(expression) == ("beta",)


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("NOT (alpha AND beta) AND gamma", ("gamma",)),
        ("alpha AND NOT (beta OR gamma)", ("alpha",)),
        ("NOT NOT alpha", ("alpha",)),
        ("NOT alpha AND beta", ("beta",)),
        ("NOT alpha OR beta", ("",)),
        ("NOT (alpha AND beta)", ("",)),
    ],
)
def test_candidate_seeds_cover_positive_branches_through_negation(expression, expected):
    assert candidate_terms(parse_expression(expression)) == expected


def test_native_provider_plans_render_grouped_boolean_logic_and_universes():
    expression = 'alpha OR beta AND NOT "gamma delta"'
    plans = {
        "pubmed": PubMedProvider().plan(SearchRequest(expression)).translated_query,
        "europe_pmc": EuropePMCProvider().plan(SearchRequest(expression)).translated_query,
        "openalex": OpenAlexProvider().plan(SearchRequest(expression)).translated_query,
    }
    assert plans == {
        "pubmed": '(alpha OR (beta NOT "gamma delta"))',
        "europe_pmc": '(alpha OR (beta NOT "gamma delta"))',
        "openalex": 'alpha OR (beta NOT "gamma delta")',
    }

    pure_not = {
        "pubmed": PubMedProvider().plan(SearchRequest("NOT alpha")).translated_query,
        "europe_pmc": EuropePMCProvider().plan(SearchRequest("NOT alpha")).translated_query,
        "openalex": OpenAlexProvider().plan(SearchRequest("NOT alpha")).translated_query,
    }
    assert pure_not == {
        "pubmed": "((all[sb] NOT alpha))",
        "europe_pmc": "((* NOT alpha))",
        "openalex": "NOT alpha",
    }


def test_crossref_plain_keyword_search_keeps_ranked_query_plan():
    provider = CrossrefProvider(min_interval=0)
    plan = provider.plan(SearchRequest("lung cancer treatment"))

    assert plan.parameters["query"] == "lung cancer treatment"
    assert "boolean_expression" not in plan.parameters
    assert "candidate_queries" not in plan.parameters


def test_query_preview_reports_normalized_expression_and_crossref_execution_mode():
    provider = CrossrefProvider(min_interval=0)
    preview = QueryPreviewService(ProviderRegistry((provider,))).preview(
        'alpha OR beta AND NOT "gamma delta"', ("crossref",)
    )

    assert preview.valid is True
    assert preview.normalized_expression == 'alpha OR (beta AND (NOT "gamma delta"))'
    assert preview.providers[0].execution_mode == "metadata_filter"
    assert "titles, abstracts and subjects" in preview.providers[0].notice
    with pytest.raises(SearchExpressionError) as error:
        QueryPreviewService(ProviderRegistry((provider,))).preview("alpha OR", ("crossref",))
    assert error.value.position > 0


@pytest.mark.asyncio
async def test_crossref_boolean_cursor_continues_candidate_branches_and_retains_raw_pages():
    calls = []

    def handler(request):
        calls.append((request.url.params.get("query"), request.url.params["cursor"]))
        query, cursor = calls[-1]
        if query == "alpha" and cursor == "*":
            return _response(
                request,
                [_work("10.5555/alpha", "Alpha result")],
                next_cursor="alpha-next",
            )
        if query == "alpha" and cursor == "alpha-next":
            return _response(request, [])
        assert query == "beta" and cursor == "*"
        return _response(request, [_work("10.5555/beta", "Beta result")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("alpha OR beta", page_size=1))
    first = await provider.search(plan)
    second = await provider.search(plan, cursor=first.next_cursor)
    third = await provider.search(plan, cursor=second.next_cursor)

    assert calls == [("alpha", "*"), ("alpha", "alpha-next"), ("beta", "*")]
    assert [record.source_id for record in first.records] == ["10.5555/alpha"]
    assert second.records == ()
    assert third.records[0].source_id == "10.5555/beta"
    assert first.raw_responses[0].body.startswith(b'{"message"')
    assert first.raw_responses[0].safe_request_metadata["execution_mode"] == "metadata_filter"
    assert first.raw_responses[0].safe_request_metadata["candidate_branch"] == "0"
    assert second.raw_responses[0].safe_request_metadata["cursor"] == "alpha-next"
    assert third.raw_responses[0].safe_request_metadata["candidate_branch"] == "1"
    assert third.next_cursor is None
    await client.aclose()


@pytest.mark.asyncio
async def test_crossref_filtered_empty_full_page_keeps_candidate_cursor_and_raw_snapshot():
    body_seen = []

    def handler(request):
        response = _response(
            request,
            [_work("10.5555/alpha", "Alpha only")],
            next_cursor="alpha-next",
        )
        body_seen.append(response.content)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("alpha AND beta", page_size=1))
    page = await provider.search(plan)

    assert page.records == ()
    assert page.next_cursor.startswith("boolean-v1:")
    assert page.raw_responses[0].body == body_seen[0]
    assert page.raw_responses[0].safe_request_metadata["query"] == "alpha"
    assert page.raw_responses[0].safe_request_metadata["boolean_expression"] == "alpha AND beta"
    await client.aclose()


@pytest.mark.asyncio
async def test_crossref_negative_only_expression_scans_catalog_without_query_filter():
    seen = []

    def handler(request):
        seen.append(request)
        return _response(
            request,
            [
                _work("10.5555/other", "Prohibited topic"),
                _work("10.5555/match", "Treatment advances"),
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("NOT prohibited"))
    page = await provider.search(plan)

    assert plan.parameters["candidate_queries"] == '[""]'
    assert "query" not in seen[0].url.params
    assert [record.source_id for record in page.records] == ["10.5555/match"]
    assert page.raw_responses[0].safe_request_metadata["query"] == ""
    await client.aclose()


@pytest.mark.asyncio
async def test_crossref_boolean_cursor_rejects_malformed_and_repeated_source_cursors():
    def bad_cursor_handler(request):
        return _response(request, [])

    client = httpx.AsyncClient(transport=httpx.MockTransport(bad_cursor_handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("alpha AND beta", page_size=1))
    with pytest.raises(ValueError, match="invalid Crossref Boolean cursor"):
        await provider.search(plan, cursor="boolean-v1:not-base64!")
    await client.aclose()

    def repeated_handler(request):
        return _response(
            request,
            [_work("10.5555/alpha", "Alpha beta")],
            next_cursor="same-source-cursor",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(repeated_handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("alpha AND beta", page_size=1))
    cursor = "boolean-v1:" + base64.urlsafe_b64encode(
        json.dumps([0, "same-source-cursor"], separators=(",", ":")).encode()
    ).decode()
    with pytest.raises(ProviderResponseError, match="repeated a candidate cursor"):
        await provider.search(plan, cursor=cursor)
    await client.aclose()


@pytest.mark.asyncio
async def test_crossref_long_valid_or_expression_does_not_overflow_on_local_parse():
    expression = " OR ".join(f"term{index}" for index in range(120))
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.params["query"] == "term0"
        return _response(request, [])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest(expression, page_size=1))
    page = await provider.search(plan)

    assert len(seen) == 1
    assert page.records == ()
    assert page.next_cursor.startswith("boolean-v1:")
    assert plan.parameters["boolean_expression"] == expression
    await client.aclose()


@pytest.mark.asyncio
async def test_crossref_double_not_stays_in_local_metadata_filter():
    seen = []

    def handler(request):
        seen.append(request)
        return _response(
            request,
            [
                _work("10.5555/no", "A paper about another topic"),
                _work("10.5555/yes", "An alpha paper"),
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CrossrefProvider(client=client, min_interval=0)
    plan = provider.plan(SearchRequest("NOT NOT alpha", page_size=2))
    page = await provider.search(plan)

    assert plan.parameters["candidate_queries"] == '["alpha"]'
    assert plan.parameters["boolean_expression"] == "NOT NOT alpha"
    assert seen[0].url.params["query"] == "alpha"
    assert [record.source_id for record in page.records] == ["10.5555/yes"]
    await client.aclose()
