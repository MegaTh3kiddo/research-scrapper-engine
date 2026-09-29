"""Europe PMC literature search adapter.

The adapter speaks Europe PMC's REST search and article endpoints while
returning only the provider-neutral records defined by the core ports.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import html
import json
import re
from typing import Any
from urllib.parse import quote

import httpx

from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.domain.search_expression import parse_expression, render_expression
from research_engine.infrastructure.providers.http import (
    ProviderRequestError,
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


_DEFAULT_BASE_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_TRANSLATION_VERSION = "europe-pmc-boolean-v2"
_MAX_PAGE_SIZE = 1000
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
_HTML_TAG_RE = re.compile(r"<[^>]*>")


class EuropePMCProvider:
    """Search Europe PMC with cursor pagination and normalized metadata.

    A plain ``httpx.AsyncClient`` can be injected for shared lifecycle and
    testing. The provider wraps it in the common pacing/retry client. Passing an
    existing ``RateLimitedHTTPClient`` shares that provider-wide limiter.
    Instances created without a client own their underlying HTTP session; call
    :meth:`aclose` when finished.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | RateLimitedHTTPClient | None = None,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        min_interval: float = 0.34,
        clock: Callable[[], datetime] | None = None,
        proxy_url: str | None = None,
    ) -> None:
        if isinstance(client, RateLimitedHTTPClient):
            if proxy_url is not None:
                raise ValueError("cannot configure a proxy with an injected HTTP client")
            self._http = client
        else:
            self._http = RateLimitedHTTPClient(
                base_url,
                min_interval=min_interval,
                client=client,
                proxy_url=proxy_url,
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def name(self) -> str:
        return "europe_pmc"

    @property
    def parser_version(self) -> str:
        return "europe-pmc-parser-v2"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_filters=frozenset(
                {
                    "published_from",
                    "published_to",
                    "languages",
                    "publication_types",
                    "open_access_only",
                }
            ),
            supports_boolean_queries=True,
        )

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        if request.page_size > _MAX_PAGE_SIZE:
            raise ValueError(
                f"Europe PMC page_size cannot exceed {_MAX_PAGE_SIZE}"
            )

        filters = request.filters
        clauses: list[str] = []
        unsupported: list[str] = []
        if filters.published_from is not None and filters.published_to is not None:
            start = filters.published_from.isoformat() if filters.published_from else "*"
            end = filters.published_to.isoformat() if filters.published_to else "*"
            clauses.append(f"FIRST_PDATE:[{start} TO {end}]")
        elif filters.published_from is not None:
            unsupported.append("published_from")
        elif filters.published_to is not None:
            unsupported.append("published_to")

        if filters.languages:
            clauses.append(_multi_value_filter("LANG", filters.languages))
        if filters.publication_types:
            clauses.append(_multi_value_filter("PUB_TYPE", filters.publication_types))
        if filters.open_access_only is True:
            clauses.append("OPEN_ACCESS:y")

        expression = render_expression(parse_expression(request.expression), provider=self.name)
        query = f"({expression})"
        if clauses:
            query += " AND " + " AND ".join(f"({clause})" for clause in clauses)

        if filters.open_access_only is False:
            unsupported.append("open_access_only")
        if filters.humans_only is not None:
            unsupported.append("humans_only")

        return ProviderQueryPlan(
            provider=self.name,
            translated_query=query,
            translation_version=_TRANSLATION_VERSION,
            unsupported_filters=tuple(unsupported),
            parameters={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": str(request.page_size),
            },
        )

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage:
        self._validate_plan(plan)
        params = dict(plan.parameters)
        params.setdefault("query", plan.translated_query)
        params.setdefault("format", "json")
        params.setdefault("resultType", "core")
        params.setdefault("pageSize", "100")
        params["cursorMark"] = cursor or "*"

        response = await self._get("search", params)
        raw_response = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": "search",
                "query": params["query"],
                "cursor_mark": params["cursorMark"],
                "page_size": params["pageSize"],
                "result_type": params["resultType"],
            },
        )
        payload = _parse_json(response)
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("Europe PMC search response must be an object")

        result_list = payload.get("resultList")
        if not isinstance(result_list, Mapping):
            raise ProviderResponseError("Europe PMC search response has no resultList")
        results = result_list.get("result", [])
        if results is None:
            results = []
        if not isinstance(results, list):
            raise ProviderResponseError("Europe PMC resultList.result must be a list")

        records = tuple(
            self._normalize_record(item, response_index=0)
            for item in results
            if isinstance(item, Mapping)
        )
        if len(records) != len(results):
            raise ProviderResponseError("Europe PMC returned a malformed result item")

        total_count = _parse_nonnegative_int(payload.get("hitCount"), "hitCount")
        next_cursor = payload.get("nextCursorMark")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ProviderResponseError("Europe PMC nextCursorMark must be a string")
        if not records or not next_cursor or (
            next_cursor == params["cursorMark"] and len(records) < int(params["pageSize"])
        ):
            next_cursor = None

        return SearchPage(
            records=records,
            raw_responses=(raw_response,),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    async def fetch(self, source_id: str) -> RecordResult:
        source, article_id = _split_source_id(source_id)
        path = f"article/{quote(source, safe='')}/{quote(article_id, safe='')}"
        params = {"format": "json", "resultType": "core"}
        response = await self._get(path, params)
        raw_response = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": path,
                "source_id": f"{source}:{article_id}",
                "result_type": params["resultType"],
            },
        )
        payload = _parse_json(response)
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("Europe PMC article response must be an object")

        article = payload.get("result", payload.get("article", payload))
        if not isinstance(article, Mapping):
            raise ProviderResponseError("Europe PMC article response has no article object")
        # The article endpoint may omit source/id in a wrapped response. Its
        # request identity is authoritative in that case.
        article_data = dict(article)
        article_data.setdefault("source", source)
        article_data.setdefault("id", article_id)
        record = self._normalize_record(article_data, response_index=0)
        if record.source_id != f"{source}:{article_id}":
            raise ProviderResponseError("Europe PMC returned a different source record")
        return RecordResult(record=record, raw_response=raw_response)

    async def aclose(self) -> None:
        """Close the underlying session if this provider owns it."""
        await self._http.aclose()

    async def _get(self, path: str, params: Mapping[str, str]) -> httpx.Response:
        try:
            response = await self._http.get(path, params=params)
        except ProviderRequestError:
            raise
        except httpx.RequestError:
            raise ProviderRequestError("Europe PMC request failed") from None

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderRequestError(
                f"Europe PMC returned HTTP {exc.response.status_code}"
            ) from exc
        return response

    def _raw_response(
        self, response: httpx.Response, metadata: Mapping[str, str]
    ) -> RawResponse:
        media_type = response.headers.get("content-type", "application/json")
        media_type = media_type.split(";", maxsplit=1)[0].strip() or "application/json"
        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return RawResponse(
            body=bytes(response.content),
            media_type=media_type,
            fetched_at=fetched_at,
            safe_request_metadata=dict(metadata),
        )

    @staticmethod
    def _validate_plan(plan: ProviderQueryPlan) -> None:
        if plan.provider != "europe_pmc":
            raise ValueError(f"query plan is for {plan.provider}, not europe_pmc")

    @staticmethod
    def _normalize_record(
        item: Mapping[str, Any], *, response_index: int
    ) -> ProviderRecord:
        source = _clean_text(item.get("source"))
        source_id = _clean_text(item.get("id"))
        if not source or not source_id:
            raise ProviderResponseError(
                "Europe PMC article record must include source and id"
            )

        title = _clean_text(item.get("title"))
        if not title:
            raise ProviderResponseError("Europe PMC article record must include a title")

        identifiers: list[Identifier] = []
        pmid = _clean_text(item.get("pmid"))
        pmcid = _clean_text(item.get("pmcid"))
        doi = _normalize_doi(item.get("doi") or item.get("DOI"))
        if not pmid and source.upper() == "MED":
            pmid = source_id
        if not pmcid and source.upper() == "PMC":
            pmcid = source_id
        if pmid:
            identifiers.append(Identifier("pmid", pmid))
        if pmcid:
            identifiers.append(Identifier("pmcid", pmcid))
        if doi:
            identifiers.append(Identifier("doi", doi))

        journal = _clean_text(item.get("journalTitle"))
        if not journal:
            journal_info = item.get("journalInfo")
            if isinstance(journal_info, Mapping):
                journal_data = journal_info.get("journal")
                if isinstance(journal_data, Mapping):
                    journal = _clean_text(journal_data.get("title"))

        return ProviderRecord(
            source_id=f"{source}:{source_id}",
            source_database=source,
            response_index=response_index,
            title=title,
            identifiers=tuple(identifiers),
            authors=_authors(item),
            journal_name=journal or None,
            publication_date=_publication_date(
                item.get("firstPublicationDate"), item.get("pubYear")
            ),
            abstract=_clean_text(item.get("abstractText")) or None,
            url=(
                "https://europepmc.org/article/"
                f"{quote(source, safe='')}/{quote(source_id, safe='')}"
            ),
            keywords=_keywords(item),
            citation_count=_optional_nonnegative_count(
                item.get("citedByCount"), "citedByCount"
            ),
            publication_types=_publication_types(item),
        )


def _multi_value_filter(field: str, values: Sequence[str]) -> str:
    clauses = [f'{field}:"{_escape_query_value(value)}"' for value in values]
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def _escape_query_value(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("search filter values must be non-empty")
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderResponseError("Europe PMC returned invalid JSON") from exc


def _parse_nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError(f"Europe PMC {field} must be an integer") from exc
    if result < 0:
        raise ProviderResponseError(f"Europe PMC {field} must be non-negative")
    return result


def _optional_nonnegative_count(value: Any, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ProviderResponseError(
            f"Europe PMC {field} must be a non-negative integer"
        )
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdecimal():
        result = int(value.strip())
    else:
        raise ProviderResponseError(
            f"Europe PMC {field} must be a non-negative integer"
        )
    if result < 0:
        raise ProviderResponseError(
            f"Europe PMC {field} must be a non-negative integer"
        )
    return result


def _publication_types(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw_types = item.get("pubTypeList")
    if isinstance(raw_types, Mapping):
        raw_types = raw_types.get("pubType")
    if raw_types is None:
        raw_types = item.get("pubType")
    if raw_types is None:
        return ()
    if isinstance(raw_types, str):
        raw_values = [raw_types]
    elif isinstance(raw_types, list):
        raw_values = raw_types
    else:
        raise ProviderResponseError("Europe PMC publication types must be a list or string")

    labels: list[str] = []
    for raw_value in raw_values:
        if isinstance(raw_value, Mapping):
            raw_value = raw_value.get("name") or raw_value.get("pubType")
        if not isinstance(raw_value, str):
            raise ProviderResponseError("Europe PMC publication type must be a string")
        if label := _clean_text(raw_value):
            labels.append(label)
    return tuple(dict.fromkeys(labels))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _normalize_doi(value: Any) -> str:
    doi = _clean_text(value)
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def _authors(item: Mapping[str, Any]) -> tuple[str, ...]:
    author_list = item.get("authorList")
    candidates: Any = author_list
    if isinstance(author_list, Mapping):
        candidates = author_list.get("author")
    if isinstance(candidates, Mapping):
        candidates = [candidates]
    if isinstance(candidates, list):
        normalized: list[str] = []
        for author in candidates:
            if isinstance(author, Mapping):
                name = _clean_text(author.get("fullName") or author.get("authorString"))
                if not name:
                    first = _clean_text(author.get("firstName"))
                    last = _clean_text(author.get("lastName"))
                    initials = _clean_text(author.get("initials"))
                    name = " ".join(part for part in (first or initials, last) if part)
            else:
                name = _clean_text(author)
            if name:
                normalized.append(name)
        if normalized:
            return tuple(normalized)

    author_string = _clean_text(item.get("authorString"))
    if not author_string:
        return ()
    return tuple(part.strip() for part in author_string.split(",") if part.strip())


def _publication_date(first_publication_date: Any, pub_year: Any) -> PartialDate | None:
    raw_date = _clean_text(first_publication_date)
    if raw_date:
        match = _DATE_RE.match(raw_date)
        if match:
            year, month, day = match.groups()
            try:
                if day:
                    return PartialDate(int(year), int(month), int(day))
                if month:
                    return PartialDate(int(year), int(month))
                return PartialDate(int(year))
            except ValueError:
                pass

    if pub_year is not None:
        try:
            return PartialDate(int(pub_year))
        except (TypeError, ValueError):
            return None
    return None


def _keywords(item: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    keyword_list = item.get("keywordList")
    if isinstance(keyword_list, Mapping):
        keyword_list = keyword_list.get("keyword")
    if isinstance(keyword_list, (list, tuple)):
        values.extend(_clean_text(value) for value in keyword_list)
    elif keyword_list is not None:
        values.append(_clean_text(keyword_list))

    mesh_list = item.get("meshHeadingList")
    if isinstance(mesh_list, Mapping):
        mesh_list = mesh_list.get("meshHeading")
    if isinstance(mesh_list, (list, tuple)):
        for term in mesh_list:
            if isinstance(term, Mapping):
                values.append(
                    _clean_text(term.get("descriptorName") or term.get("meshHeading"))
                )
            else:
                values.append(_clean_text(term))
    elif mesh_list is not None:
        values.append(_clean_text(mesh_list))

    return tuple(dict.fromkeys(value for value in values if value))


def _split_source_id(source_id: str) -> tuple[str, str]:
    if not source_id or ":" not in source_id:
        raise ValueError("source_id must use the Europe PMC SOURCE:ID format")
    source, article_id = source_id.split(":", maxsplit=1)
    source, article_id = source.strip(), article_id.strip()
    if not source or not article_id:
        raise ValueError("source_id must include both source and article ID")
    return source, article_id
