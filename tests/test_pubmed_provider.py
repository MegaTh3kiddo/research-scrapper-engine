import json
from datetime import date
from urllib.parse import urlparse

import pytest

from research_engine.domain.value_objects import SearchFilters
from research_engine.infrastructure.providers.http import ProviderLimitError, ProviderResponseError
from research_engine.infrastructure.providers.pubmed import PubMedProvider
from research_engine.ports.providers import SearchRequest


class FakeResponse:
    def __init__(self, body: bytes, media_type: str) -> None:
        self.content = body
        self.headers = {"content-type": media_type}
        self.status_code = 200


class QueueClient:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get(self, path: str, *, params: dict[str, str]) -> FakeResponse:
        self.calls.append((urlparse(path).path.rsplit("/", 1)[-1], params))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


def esearch(count: int, *ids: str) -> FakeResponse:
    payload = {"esearchresult": {"count": str(count), "idlist": list(ids)}}
    return FakeResponse(json.dumps(payload).encode(), "application/json; charset=UTF-8")


def efetch(*records: str) -> FakeResponse:
    body = "<PubmedArticleSet>" + "".join(records) + "</PubmedArticleSet>"
    return FakeResponse(body.encode(), "application/xml; charset=UTF-8")


def article(
    pmid: str = "12345",
    *,
    title: str = "A <i>useful</i> study",
    pub_date: str = "<Year>2024</Year><Month>Feb</Month><Day>29</Day>",
    publication_types: tuple[str, ...] = (),
    publication_status: str | None = None,
) -> str:
    publication_type_list = "".join(
        f'<PublicationType UI="test">{label}</PublicationType>'
        for label in publication_types
    )
    publication_type_list = (
        f"<PublicationTypeList>{publication_type_list}</PublicationTypeList>"
        if publication_types else ""
    )
    publication_status_element = (
        f"<PublicationStatus>{publication_status}</PublicationStatus>"
        if publication_status else ""
    )
    return f"""<PubmedArticle>
      <MedlineCitation><PMID>{pmid}</PMID>
        <Article>
          <ArticleTitle>{title}</ArticleTitle>
          {publication_type_list}
          <Journal><Title>Journal of Tests</Title><JournalIssue><PubDate>{pub_date}</PubDate></JournalIssue></Journal>
          <AuthorList>
            <Author><LastName>Doe</LastName><ForeName>Jane A</ForeName></Author>
            <Author><CollectiveName>Study Group</CollectiveName></Author>
          </AuthorList>
          <Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText><AbstractText>Second part.</AbstractText></Abstract>
        </Article>
        <KeywordList><Keyword>oncology</Keyword><Keyword>clinical research</Keyword></KeywordList>
      </MedlineCitation>
      <PubmedData>{publication_status_element}<ArticleIdList><ArticleId IdType="doi">10.1000/example</ArticleId><ArticleId IdType="pmc">PMC42</ArticleId></ArticleIdList></PubmedData>
    </PubmedArticle>"""


def test_plan_translates_supported_filters_and_discloses_unsupported_ones():
    provider = PubMedProvider(client=QueueClient())
    request = SearchRequest(
        "breast cancer immunotherapy",
        SearchFilters(
            published_from=date(2020, 1, 2),
            published_to=date(2024, 12, 31),
            languages=("en", "French"),
            publication_types=("Review", "Clinical Trial"),
            humans_only=True,
            open_access_only=True,
        ),
        page_size=500,
    )

    plan = provider.plan(request)

    assert plan.provider == "pubmed"
    assert plan.translation_version == "pubmed-boolean-v2"
    assert '"2020/01/02:2024/12/31"' not in plan.translated_query
    assert "2020/01/02:2024/12/31[dp]" in plan.translated_query
    assert '"English"[lang]' in plan.translated_query
    assert '"French"[lang]' in plan.translated_query
    assert '"Clinical Trial"[pt]' in plan.translated_query
    assert "Humans[MeSH Terms]" in plan.translated_query
    assert plan.parameters == {"page_size": "200"}
    assert plan.unsupported_filters == ("open_access_only",)


@pytest.mark.asyncio
async def test_search_keeps_search_and_fetch_provenance_and_normalizes_record():
    client = QueueClient(esearch(3, "12345", "67890"), efetch(article(), article("67890", title="Another study")))
    provider = PubMedProvider(client=client, api_key="secret-key", email="researcher@example.org")
    plan = provider.plan(SearchRequest("cancer"))

    page = await provider.search(plan)

    assert [call[0] for call in client.calls] == ["esearch.fcgi", "efetch.fcgi"]
    assert client.calls[0][1]["db"] == "pubmed"
    assert client.calls[0][1]["retmode"] == "json"
    assert client.calls[0][1]["retmax"] == "100"
    assert client.calls[0][1]["api_key"] == "secret-key"
    assert len(page.raw_responses) == 2
    assert [raw.media_type for raw in page.raw_responses] == ["application/json", "application/xml"]
    assert all("api_key" not in raw.safe_request_metadata for raw in page.raw_responses)
    assert all("email" not in raw.safe_request_metadata for raw in page.raw_responses)
    assert page.total_count == 3
    assert page.next_cursor == "2"
    assert page.records[0].response_index == 1
    record = page.records[0]
    assert record.source_id == "12345"
    assert record.source_database == "pubmed"
    assert record.title == "A useful study"
    assert record.authors == ("Jane A Doe", "Study Group")
    assert record.journal_name == "Journal of Tests"
    assert record.publication_date.year == 2024
    assert record.publication_date.month == 2
    assert record.publication_date.day == 29
    assert record.abstract == "BACKGROUND: First part.\nSecond part."
    assert record.keywords == ("oncology", "clinical research")
    assert {(identifier.scheme, identifier.value) for identifier in record.identifiers} == {
        ("pmid", "12345"),
        ("doi", "10.1000/example"),
        ("pmcid", "PMC42"),
    }


@pytest.mark.asyncio
async def test_search_uses_cursor_and_caps_at_pubmed_result_window():
    client = QueueClient(esearch(10_500, "9801", "9802"), efetch(article("9801"), article("9802")))
    provider = PubMedProvider(client=client)
    plan = provider.plan(SearchRequest("cancer", page_size=500))

    page = await provider.search(plan, cursor="9800")

    assert client.calls[0][1]["retstart"] == "9800"
    assert client.calls[0][1]["retmax"] == "200"
    assert page.total_count == 10_500
    assert page.next_cursor == "9802"

    final_client = QueueClient(esearch(10_500, "9999", "10000"), efetch(article("9999"), article("10000")))
    final_page = await PubMedProvider(client=final_client).search(plan, cursor="9998")
    assert final_client.calls[0][1]["retmax"] == "2"
    assert final_page.next_cursor == "10000"
    assert final_page.total_count > 10_000

    with pytest.raises(ProviderLimitError, match="10,000"):
        await PubMedProvider(client=QueueClient()).search(plan, cursor="10000")


@pytest.mark.asyncio
async def test_empty_search_retains_esearch_snapshot_without_fetching():
    client = QueueClient(esearch(0))
    page = await PubMedProvider(client=client).search(
        PubMedProvider(client=QueueClient()).plan(SearchRequest("rare condition"))
    )

    assert len(client.calls) == 1
    assert page.records == ()
    assert page.total_count == 0
    assert page.next_cursor is None
    assert len(page.raw_responses) == 1


@pytest.mark.asyncio
async def test_fetch_returns_single_record_and_snapshot():
    client = QueueClient(efetch(article("555")))

    result = await PubMedProvider(client=client).fetch("555")

    assert result.record.source_id == "555"
    assert result.record.response_index == 0
    assert result.raw_response.body.startswith(b"<PubmedArticleSet>")


@pytest.mark.asyncio
async def test_search_rejects_efetch_records_that_do_not_match_requested_pmids():
    client = QueueClient(esearch(2, "12345", "67890"), efetch(article("12345")))
    plan = PubMedProvider(client=client).plan(SearchRequest("cancer"))

    with pytest.raises(ProviderResponseError, match="do not match ESearch IDs"):
        await PubMedProvider(client=client).search(plan)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeResponse(b"not json", "application/json"), "malformed JSON"),
        (FakeResponse(b"<broken", "application/xml"), "malformed XML"),
    ],
)
async def test_malformed_source_payloads_raise_response_error(response, message):
    client = QueueClient(response)
    provider = PubMedProvider(client=client)
    plan = provider.plan(SearchRequest("cancer"))

    if message == "malformed JSON":
        with pytest.raises(ProviderResponseError, match=message):
            await provider.search(plan)
    else:
        with pytest.raises(ProviderResponseError, match=message):
            await PubMedProvider(client=QueueClient(esearch(1, "12345"), response)).search(plan)


@pytest.mark.asyncio
async def test_efetch_rejects_entity_declarations():
    unsafe_xml = FakeResponse(
        b'<!DOCTYPE x [<!ENTITY e "expanded">]><PubmedArticleSet/>',
        "application/xml",
    )
    plan = PubMedProvider(client=QueueClient()).plan(SearchRequest("cancer"))

    with pytest.raises(ProviderResponseError, match="malformed XML"):
        await PubMedProvider(client=QueueClient(esearch(1, "12345"), unsafe_xml)).search(plan)


@pytest.mark.asyncio
async def test_efetch_accepts_standard_pubmed_doctype_without_entities():
    body = (
        '<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" '
        '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">'
        '<PubmedArticleSet>' + article("12345") + '</PubmedArticleSet>'
    )
    client = QueueClient(esearch(1, "12345"), FakeResponse(body.encode(), "application/xml"))
    provider = PubMedProvider(client=client)

    page = await provider.search(provider.plan(SearchRequest("cancer")))

    assert page.records[0].source_id == "12345"


@pytest.mark.asyncio
async def test_year_only_publication_date_keeps_unknown_month_and_day():
    client = QueueClient(esearch(1, "12345"), efetch(article(pub_date="<Year>2023</Year>")))
    provider = PubMedProvider(client=client)

    page = await provider.search(provider.plan(SearchRequest("rare disease")))

    assert page.records[0].publication_date.year == 2023
    assert page.records[0].publication_date.month is None
    assert page.records[0].publication_date.day is None


@pytest.mark.asyncio
async def test_multiple_pubmed_publication_types_and_explicit_publication_status():
    client = QueueClient(
        esearch(1, "12345"),
        efetch(
            article(
                publication_types=("Journal Article", "Meta-Analysis"),
                publication_status="aheadofprint",
            )
        ),
    )
    provider = PubMedProvider(client=client)

    page = await provider.search(provider.plan(SearchRequest("cancer")))

    record = page.records[0]
    assert record.publication_types == ("Journal Article", "Meta-Analysis")
    assert record.publication_status == "aheadofprint"
    assert record.citation_count is None


@pytest.mark.asyncio
async def test_referenced_article_identifiers_are_not_asserted_as_self_identifiers():
    body = article("12345").replace(
        "</PubmedData>",
        """<ReferenceList><Reference><Citation>Another paper</Citation><ArticleIdList>
        <ArticleId IdType="pubmed">67890</ArticleId>
        <ArticleId IdType="doi">10.1000/other</ArticleId>
        <ArticleId IdType="pmc">PMC999</ArticleId>
        </ArticleIdList></Reference></ReferenceList></PubmedData>""",
    )
    provider = PubMedProvider(client=QueueClient(esearch(1, "12345"), efetch(body)))

    page = await provider.search(provider.plan(SearchRequest("cancer")))

    assert provider.parser_version == "pubmed-parser-v3"
    assert {(item.scheme, item.value) for item in page.records[0].identifiers} == {
        ("pmid", "12345"), ("doi", "10.1000/example"), ("pmcid", "PMC42"),
    }


@pytest.mark.asyncio
async def test_conflicting_top_level_pubmed_id_is_rejected():
    body = article("12345").replace(
        '<ArticleId IdType="doi">',
        '<ArticleId IdType="pubmed">67890</ArticleId><ArticleId IdType="doi">',
        1,
    )
    provider = PubMedProvider(client=QueueClient(esearch(1, "12345"), efetch(body)))

    with pytest.raises(ProviderResponseError, match="conflicting PubMed ID"):
        await provider.search(provider.plan(SearchRequest("cancer")))
