"""PubMed E-utilities provider implementation."""

from __future__ import annotations

from datetime import UTC, date, datetime
import re
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from typing import Any

from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.domain.search_expression import parse_expression, render_expression
from research_engine.infrastructure.providers.http import (
    ProviderLimitError,
    ProviderResponseError,
    RateLimitedHTTPClient,
)
from research_engine.ports.providers import (
    ProviderCapabilities,
    ProviderQueryPlan,
    ProviderRecord,
    RawResponse,
    RecordResult,
    SearchPage,
    SearchRequest,
)


_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_MAX_PUBMED_RESULTS = 10_000
_MAX_EFETCH_BATCH = 200
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_LANGUAGE_CODES = {
    "ar": "Arabic",
    "chi": "Chinese",
    "de": "German",
    "deu": "German",
    "en": "English",
    "eng": "English",
    "es": "Spanish",
    "est": "Estonian",
    "fa": "Persian",
    "fr": "French",
    "fra": "French",
    "he": "Hebrew",
    "hin": "Hindi",
    "it": "Italian",
    "ita": "Italian",
    "ja": "Japanese",
    "jpn": "Japanese",
    "ko": "Korean",
    "kor": "Korean",
    "nl": "Dutch",
    "nld": "Dutch",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "tr": "Turkish",
    "tur": "Turkish",
}
_SAFE_FILTER_VALUE = re.compile(r"^[A-Za-z][A-Za-z -]*$")


class PubMedProvider:
    """Search and fetch PubMed records through NCBI E-utilities.

    ``client`` can be an ``httpx.AsyncClient`` compatible object or the shared
    rate limited client. Credentials are sent only to the E-utilities API and
    are excluded from retained request metadata.
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "research_engine",
        proxy_url: str | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._email = email.strip() if email and email.strip() else None
        self._tool = tool.strip() or "research_engine"
        # NCBI permits 3 requests/second without a key and 10/second with one.
        # Even an injected HTTP session must pass through the same limiter.
        interval = 0.34 if self._api_key is None else 0.11
        if isinstance(client, RateLimitedHTTPClient) and proxy_url is not None:
            raise ValueError("cannot configure a proxy with an injected HTTP client")
        self._client = (
            client
            if isinstance(client, RateLimitedHTTPClient)
            else RateLimitedHTTPClient(
                _BASE_URL, min_interval=interval, client=client, proxy_url=proxy_url,
            )
        )

    @property
    def name(self) -> str:
        return "pubmed"

    @property
    def parser_version(self) -> str:
        return "pubmed-parser-v3"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_filters=frozenset(
                {
                    "published_from",
                    "published_to",
                    "languages",
                    "publication_types",
                    "humans_only",
                }
            ),
            supports_boolean_queries=True,
        )

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        """Translate portable filters into PubMed field-qualified clauses."""

        filters = request.filters
        expression = render_expression(parse_expression(request.expression), provider=self.name)
        clauses = [f"({expression})"]
        unsupported: list[str] = []

        if filters.published_from is not None or filters.published_to is not None:
            lower = (
                filters.published_from.strftime("%Y/%m/%d")
                if filters.published_from
                else "1800/01/01"
            )
            upper = (
                filters.published_to.strftime("%Y/%m/%d")
                if filters.published_to
                else "3000/12/31"
            )
            clauses.append(f"({lower}:{upper}[dp])")

        language_terms: list[str] = []
        for language in filters.languages:
            normalized = language.strip()
            language_name = _LANGUAGE_CODES.get(normalized.lower(), normalized)
            if not _SAFE_FILTER_VALUE.fullmatch(language_name):
                unsupported.append("languages")
                language_terms.clear()
                break
            language_terms.append(f'"{language_name}"[lang]')
        if language_terms:
            clauses.append("(" + " OR ".join(language_terms) + ")")

        type_terms: list[str] = []
        for publication_type in filters.publication_types:
            normalized = publication_type.strip()
            if not _SAFE_FILTER_VALUE.fullmatch(normalized):
                unsupported.append("publication_types")
                type_terms.clear()
                break
            type_terms.append(f'"{normalized}"[pt]')
        if type_terms:
            clauses.append("(" + " OR ".join(type_terms) + ")")

        if filters.humans_only is True:
            clauses.append("Humans[MeSH Terms]")
        elif filters.humans_only is False:
            # Absence of the Humans heading does not mean a record is not about
            # humans, especially for newly indexed articles.
            unsupported.append("humans_only=false")

        if filters.open_access_only is not None:
            # PubMed's Free Full Text subset is not equivalent to open access.
            unsupported.append("open_access_only")

        page_size = min(request.page_size, _MAX_EFETCH_BATCH)
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=" AND ".join(clauses),
            translation_version="pubmed-boolean-v2",
            unsupported_filters=tuple(dict.fromkeys(unsupported)),
            parameters={"page_size": str(page_size)},
        )

    async def search(self, plan: ProviderQueryPlan, cursor: str | None = None) -> SearchPage:
        if plan.provider != self.name:
            raise ValueError(f"PubMed cannot execute provider plan {plan.provider!r}")
        if plan.unsupported_filters:
            raise ValueError("cannot execute a query plan with unsupported filters")

        try:
            start = int(cursor) if cursor is not None else 0
        except ValueError as exc:
            raise ValueError("PubMed cursor must be a non-negative integer offset") from exc
        if start < 0:
            raise ValueError("PubMed cursor must be a non-negative integer offset")
        if start >= _MAX_PUBMED_RESULTS:
            raise ProviderLimitError(
                "PubMed ESearch exposes at most the first 10,000 results; narrow the query or date range"
            )

        try:
            requested_page_size = int(plan.parameters.get("page_size", "100"))
        except ValueError as exc:
            raise ValueError("PubMed plan page_size must be an integer") from exc
        if requested_page_size < 1:
            raise ValueError("PubMed plan page_size must be positive")
        page_size = min(requested_page_size, _MAX_EFETCH_BATCH, _MAX_PUBMED_RESULTS - start)

        search_params = {
            "db": "pubmed",
            "term": plan.translated_query,
            "retmode": "json",
            "retstart": str(start),
            "retmax": str(page_size),
            "tool": self._tool,
        }
        search_params.update(self._credentials())
        search_response = await self._client.get("esearch.fcgi", params=search_params)
        search_raw = self._raw_response(
            search_response,
            {
                "method": "GET",
                "endpoint": "esearch.fcgi",
                "db": "pubmed",
                "term": plan.translated_query,
                "retstart": str(start),
                "retmax": str(page_size),
            },
        )
        result = _parse_esearch(search_raw.body)
        try:
            total_count = int(result["count"])
            ids = tuple(str(pmid) for pmid in result["idlist"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderResponseError("PubMed ESearch response is missing count or ID list") from exc
        if total_count < 0 or any(not pmid.isdigit() for pmid in ids):
            raise ProviderResponseError("PubMed ESearch returned invalid count or PMID values")

        raw_responses = [search_raw]
        records: tuple[ProviderRecord, ...] = ()
        if ids:
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
                "tool": self._tool,
            }
            fetch_params.update(self._credentials())
            fetch_response = await self._client.get("efetch.fcgi", params=fetch_params)
            fetch_raw = self._raw_response(
                fetch_response,
                {
                    "method": "GET",
                    "endpoint": "efetch.fcgi",
                    "db": "pubmed",
                    "ids": ",".join(ids),
                },
            )
            raw_responses.append(fetch_raw)
            records = _parse_efetch(fetch_raw.body, response_index=1)
            returned_ids = tuple(record.source_id for record in records)
            if len(returned_ids) != len(ids) or set(returned_ids) != set(ids):
                missing = sorted(set(ids) - set(returned_ids))
                unexpected = sorted(set(returned_ids) - set(ids))
                raise ProviderResponseError(
                    "PubMed EFetch IDs do not match ESearch IDs "
                    f"(missing={missing!r}, unexpected={unexpected!r})"
                )
            records_by_id = {record.source_id: record for record in records}
            records = tuple(records_by_id[pmid] for pmid in ids)

        next_start = start + len(ids)
        next_cursor = None
        if ids and next_start < min(total_count, _MAX_PUBMED_RESULTS):
            next_cursor = str(next_start)
        elif ids and total_count > _MAX_PUBMED_RESULTS and next_start >= _MAX_PUBMED_RESULTS:
            # Keep the truncation observable to DiscoveryService and callers.
            # Following this cursor raises ProviderLimitError with recovery advice.
            next_cursor = str(_MAX_PUBMED_RESULTS)
        return SearchPage(
            records=records,
            raw_responses=tuple(raw_responses),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    async def fetch(self, source_id: str) -> RecordResult:
        pmid = source_id.strip()
        if not pmid.isdigit():
            raise ValueError("PubMed source_id must be a numeric PMID")
        params = {
            "db": "pubmed",
            "id": pmid,
            "retmode": "xml",
            "tool": self._tool,
        }
        params.update(self._credentials())
        response = await self._client.get("efetch.fcgi", params=params)
        raw = self._raw_response(
            response,
            {"method": "GET", "endpoint": "efetch.fcgi", "db": "pubmed", "ids": pmid},
        )
        records = _parse_efetch(raw.body, response_index=0)
        for record in records:
            if record.source_id == pmid:
                return RecordResult(record=record, raw_response=raw)
        raise ProviderResponseError(f"PubMed EFetch returned no record for PMID {pmid}")

    def _credentials(self) -> dict[str, str]:
        credentials = {"tool": self._tool}
        if self._api_key:
            credentials["api_key"] = self._api_key
        if self._email:
            credentials["email"] = self._email
        return credentials

    async def aclose(self) -> None:
        """Close a shared wrapper only when it owns its HTTP session."""
        await self._client.aclose()

    @staticmethod
    def _raw_response(response: Any, metadata: dict[str, str]) -> RawResponse:
        content_type = response.headers.get("content-type", "application/octet-stream")
        return RawResponse(
            body=bytes(response.content),
            media_type=content_type.split(";", maxsplit=1)[0].strip().lower(),
            fetched_at=datetime.now(UTC),
            safe_request_metadata=metadata,
        )


def _parse_esearch(body: bytes) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(body)
        result = payload["esearchresult"]
        if not isinstance(result, dict):
            raise TypeError("esearchresult is not an object")
        return result
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ProviderResponseError("PubMed ESearch returned malformed JSON") from exc


def _parse_efetch(body: bytes, *, response_index: int) -> tuple[ProviderRecord, ...]:
    try:
        root = ET.fromstring(body, forbid_dtd=False, forbid_entities=True, forbid_external=True)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise ProviderResponseError("PubMed EFetch returned malformed XML") from exc

    articles = root.findall(".//PubmedArticle")
    records: list[ProviderRecord] = []
    for article in articles:
        records.append(_parse_article(article, response_index=response_index))
    if articles and len(records) != len(articles):
        raise ProviderResponseError("PubMed EFetch response contains an invalid article")
    return tuple(records)


def _parse_article(article: ET.Element, *, response_index: int) -> ProviderRecord:
    citation = article.find("MedlineCitation")
    if citation is None:
        raise ProviderResponseError("PubMed EFetch article is missing MedlineCitation")
    pmid = _text(citation.find("PMID"))
    article_node = citation.find("Article")
    if not pmid or not pmid.isdigit() or article_node is None:
        raise ProviderResponseError("PubMed EFetch article is missing a valid PMID or Article")

    title = _text(article_node.find("ArticleTitle"))
    if not title:
        raise ProviderResponseError(f"PubMed record {pmid} has no article title")

    identifiers: list[Identifier] = [Identifier("pmid", pmid)]
    # ReferenceList also contains ArticleIdList elements for cited papers.
    # Only PubmedData's direct list identifies this PubmedArticle.
    for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):
        kind = identifier.attrib.get("IdType", "").lower()
        value = _text(identifier)
        if not kind or not value:
            continue
        if kind in {"doi", "pmc", "pubmed"}:
            if kind == "pubmed" and value != pmid:
                raise ProviderResponseError(f"PubMed record {pmid} has a conflicting PubMed ID")
            if kind == "doi":
                value = re.sub(r"^doi:\s*", "", value, flags=re.IGNORECASE).strip()
            scheme = "pmcid" if kind == "pmc" else kind
            identifiers.append(Identifier(scheme, value))
    if (doi := _text(article_node.find("ELocationID"))) and _attribute(
        article_node.find("ELocationID"), "EIdType"
    ).lower() == "doi":
        identifiers.append(Identifier("doi", re.sub(r"^doi:\s*", "", doi, flags=re.I)))
    identifiers = list(dict.fromkeys(identifiers))

    authors: list[str] = []
    for author in article_node.findall("./AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        family = _text(author.find("LastName"))
        given = _text(author.find("ForeName")) or _text(author.find("Initials"))
        name = " ".join(part for part in (given, family) if part)
        if name:
            authors.append(name)

    journal_name = _text(article_node.find("./Journal/Title")) or None
    abstract_parts: list[str] = []
    for section in article_node.findall("./Abstract/AbstractText"):
        text = _text(section)
        if not text:
            continue
        label = section.attrib.get("Label") or section.attrib.get("NlmCategory")
        abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(abstract_parts) or None

    keywords = tuple(
        dict.fromkeys(
            text
            for keyword in citation.findall("./KeywordList/Keyword")
            if (text := _text(keyword))
        )
    )

    date_value = _parse_pub_date(article_node)
    publication_types = tuple(
        dict.fromkeys(
            label
            for publication_type in article_node.findall(
                "./PublicationTypeList/PublicationType"
            )
            if (label := _text(publication_type))
        )
    )
    pubmed_data = article.find("./PubmedData")
    publication_status = _text(pubmed_data.find("PublicationStatus")) if pubmed_data is not None else ""
    return ProviderRecord(
        source_id=pmid,
        title=title,
        source_database="pubmed",
        response_index=response_index,
        identifiers=tuple(identifiers),
        authors=tuple(dict.fromkeys(authors)),
        journal_name=journal_name,
        publication_date=date_value,
        abstract=abstract,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        keywords=keywords,
        publication_types=publication_types,
        publication_status=publication_status or None,
    )


def _parse_pub_date(article: ET.Element) -> PartialDate | None:
    # PubDate is the issue publication date; ArticleDate provides a useful
    # electronic-publication fallback when the issue record is incomplete.
    candidates = [article.find("./Journal/JournalIssue/PubDate")]
    candidates.extend(article.findall("./PubDateList/PubDate"))
    candidates.extend(article.findall("./ArticleDate"))
    for candidate in candidates:
        if candidate is None:
            continue
        year_text = _text(candidate.find("Year"))
        if not year_text:
            medline_date = _text(candidate.find("MedlineDate"))
            year_match = re.search(r"\b([12]\d{3})\b", medline_date)
            if year_match:
                return PartialDate(int(year_match.group(1)))
            continue
        year_match = re.search(r"\b([12]\d{3})\b", year_text)
        if not year_match:
            continue
        year = int(year_match.group(1))
        month_text = _text(candidate.find("Month"))
        month = _month_number(month_text)
        day_text = _text(candidate.find("Day"))
        if month is not None and day_text.isdigit():
            day = int(day_text)
            try:
                return PartialDate(year, month, day)
            except ValueError:
                return PartialDate(year, month)
        if month is not None:
            return PartialDate(year, month)
        return PartialDate(year)
    return None


def _month_number(value: str) -> int | None:
    normalized = value.strip().lower()
    if normalized.isdigit():
        month = int(normalized)
        return month if 1 <= month <= 12 else None
    return _MONTHS.get(normalized[:3])


def _attribute(node: ET.Element | None, name: str) -> str:
    return node.attrib.get(name, "") if node is not None else ""


def _text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())
