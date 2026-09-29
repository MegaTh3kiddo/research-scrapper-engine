"""OpenAlex works search adapter.

OpenAlex is a broad scholarly index, rather than a medical-only database. This
adapter keeps its request syntax and record shape behind the common provider
ports and stores no credentials in provenance metadata.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

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


_BASE_URL = "https://api.openalex.org"
_MAX_PAGE_SIZE = 100
_TRANSLATION_VERSION = "openalex-boolean-v2"
_WORK_ID_RE = re.compile(r"^W\d+$", re.IGNORECASE)
_PMID_RE = re.compile(r"(?:^|/)(\d+)(?:/)?$")
_PMCID_RE = re.compile(r"(?:^|/)(PMC\d+)(?:/)?$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
_LANGUAGE_ALIASES = {
    "ara": "ar", "chi": "zh", "deu": "de", "ger": "de",
    "eng": "en", "fre": "fr", "fra": "fr", "gre": "el",
    "ell": "el", "ita": "it", "jpn": "ja", "kor": "ko",
    "dut": "nl", "nld": "nl", "per": "fa", "fas": "fa",
    "por": "pt", "rus": "ru", "spa": "es", "tur": "tr",
}
_LANGUAGE_NAMES = {
    "arabic": "ar", "chinese": "zh", "dutch": "nl", "english": "en",
    "french": "fr", "german": "de", "greek": "el", "italian": "it",
    "japanese": "ja", "korean": "ko", "persian": "fa", "portuguese": "pt",
    "russian": "ru", "spanish": "es", "turkish": "tr",
}
_WORK_TYPE_ALIASES = {
    "article": "article",
    "book": "book",
    "book chapter": "book-chapter",
    "book review": "book-review",
    "conference abstract": "conference-abstract",
    "conference paper": "conference-paper",
    "dataset": "dataset",
    "dissertation": "dissertation",
    "editorial": "editorial",
    "erratum": "erratum",
    "letter": "letter",
    "preprint": "preprint",
    "report": "report",
    "retraction": "retraction",
    "review": "review",
}


class OpenAlexProvider:
    """Search and fetch OpenAlex works using its current REST API.

    A free API key is optional for basic use and increases the API's daily
    request budget. ``email`` remains accepted for provider-factory
    compatibility; OpenAlex retired and ignores the historical ``mailto``
    parameter, so only ``api_key`` is sent. Injected clients and proxy
    configuration follow the same lifecycle as the other adapters.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | RateLimitedHTTPClient | None = None,
        *,
        api_key: str | None = None,
        email: str | None = None,
        base_url: str = _BASE_URL,
        min_interval: float = 0.1,
        clock: Callable[[], datetime] | None = None,
        proxy_url: str | None = None,
    ) -> None:
        # Current OpenAlex docs say mailto is ignored; accept it for a common
        # provider constructor signature but never send it as a dead parameter.
        del email
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
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
        return "openalex"

    @property
    def parser_version(self) -> str:
        return "openalex-works-parser-v2"

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
            raise ValueError(f"OpenAlex page_size cannot exceed {_MAX_PAGE_SIZE}")

        if _contains_unquoted_bracket_group(request.expression):
            raise ValueError(
                "OpenAlex does not support PubMed bracket field tags; "
                "rewrite the query without source-specific field syntax"
            )
        expression = render_expression(parse_expression(request.expression), provider=self.name)
        filters = request.filters
        clauses: list[str] = []
        unsupported: list[str] = []
        if filters.published_from is not None:
            clauses.append(f"from_publication_date:{filters.published_from.isoformat()}")
        if filters.published_to is not None:
            clauses.append(f"to_publication_date:{filters.published_to.isoformat()}")

        if filters.languages:
            language_codes = [_openalex_language_code(value) for value in filters.languages]
            if any(code is None for code in language_codes):
                unsupported.append("languages")
            else:
                clauses.append("language:" + "|".join(code for code in language_codes if code))

        if filters.publication_types:
            work_types = [
                _WORK_TYPE_ALIASES.get(_normalize_type(value))
                for value in filters.publication_types
            ]
            if any(work_type is None for work_type in work_types):
                # The source taxonomy differs from PubMed's; omitting an
                # unknown value would silently alter the user's selection.
                unsupported.append("publication_types")
            else:
                clauses.append("type:" + "|".join(dict.fromkeys(work_types)))

        if filters.open_access_only is not None:
            value = "true" if filters.open_access_only else "false"
            clauses.append(f"open_access.is_oa:{value}")

        if filters.humans_only is not None:
            unsupported.append("humans_only")

        params = {
            "search": expression,
            "per_page": str(request.page_size),
        }
        if clauses:
            params["filter"] = ",".join(clauses)
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=expression,
            translation_version=_TRANSLATION_VERSION,
            unsupported_filters=tuple(dict.fromkeys(unsupported)),
            parameters=params,
        )

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage:
        self._validate_plan(plan)
        params = dict(plan.parameters)
        params.setdefault("search", plan.translated_query)
        params.setdefault("per_page", "25")
        params["cursor"] = cursor or "*"
        response = await self._get("works", params)
        raw_response = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": "works",
                "query": params["search"],
                "filter": params.get("filter", ""),
                "cursor": params["cursor"],
                "page_size": params["per_page"],
            },
        )
        payload = _parse_json(response)
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("OpenAlex search response must be an object")
        results = payload.get("results")
        if not isinstance(results, list):
            raise ProviderResponseError("OpenAlex search response has no results list")
        records = tuple(
            _normalize_record(item, response_index=0)
            for item in results
            if isinstance(item, Mapping)
        )
        if len(records) != len(results):
            raise ProviderResponseError("OpenAlex returned a malformed result item")

        meta = payload.get("meta")
        if not isinstance(meta, Mapping):
            raise ProviderResponseError("OpenAlex search response has no meta object")
        total_count = _parse_nonnegative_int(meta.get("count"), "meta.count")
        next_cursor = meta.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ProviderResponseError("OpenAlex meta.next_cursor must be a string")
        if not next_cursor:
            next_cursor = None
        return SearchPage(
            records=records,
            raw_responses=(raw_response,),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    async def fetch(self, source_id: str) -> RecordResult:
        work_id = _normalize_work_id(source_id)
        response = await self._get(f"works/{quote(work_id, safe='')}", {})
        raw_response = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": f"works/{work_id}",
                "source_id": work_id,
            },
        )
        payload = _parse_json(response)
        if not isinstance(payload, Mapping):
            raise ProviderResponseError("OpenAlex work response must be an object")
        record = _normalize_record(payload, response_index=0)
        if record.source_id != work_id:
            raise ProviderResponseError("OpenAlex returned a different work record")
        return RecordResult(record=record, raw_response=raw_response)

    async def aclose(self) -> None:
        """Close the underlying HTTP session when this provider owns it."""
        await self._http.aclose()

    async def _get(
        self, path: str, params: Mapping[str, str]
    ) -> httpx.Response:
        request_params = dict(params)
        if self._api_key is not None:
            request_params["api_key"] = self._api_key
        try:
            return await self._http.get(path, params=request_params)
        except ProviderRequestError as exc:
            # Rebuild the public error using only a numeric HTTP status, so a
            # custom/injected client cannot leak a request URL or API key.
            status = re.search(r"\bHTTP\s+(\d{3})\b", str(exc), re.IGNORECASE)
            if status:
                raise ProviderRequestError(
                    f"OpenAlex returned HTTP {status.group(1)}"
                ) from None
            raise ProviderRequestError("OpenAlex request failed") from None
        except httpx.RequestError:
            raise ProviderRequestError("OpenAlex request failed") from None

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
        if plan.provider != "openalex":
            raise ValueError(f"query plan is for {plan.provider}, not openalex")
        if plan.unsupported_filters:
            raise ValueError("cannot execute a query plan with unsupported filters")


def _normalize_record(item: Mapping[str, Any], *, response_index: int) -> ProviderRecord:
    try:
        source_id = _normalize_work_id(_clean_text(item.get("id")))
    except ValueError:
        raise ProviderResponseError(
            "OpenAlex work record must include a W-prefixed work ID"
        ) from None
    title = _clean_text(item.get("title") or item.get("display_name"))
    if not title:
        raise ProviderResponseError("OpenAlex work record must include a title")

    raw_type = item.get("type")
    if raw_type is not None and not isinstance(raw_type, str):
        raise ProviderResponseError("OpenAlex work type must be a string")
    publication_types = (raw_type.strip(),) if raw_type and raw_type.strip() else ()

    raw_retracted = item.get("is_retracted")
    if raw_retracted is not None and not isinstance(raw_retracted, bool):
        raise ProviderResponseError("OpenAlex is_retracted must be a boolean")

    identifiers = [Identifier("openalex", source_id)]
    raw_ids = item.get("ids")
    ids = raw_ids if isinstance(raw_ids, Mapping) else {}
    doi = _normalize_doi(item.get("doi") or ids.get("doi"))
    pmid = _normalize_pmid(ids.get("pmid"))
    pmcid = _normalize_pmcid(ids.get("pmcid"))
    if doi:
        identifiers.append(Identifier("doi", doi))
    if pmid:
        identifiers.append(Identifier("pmid", pmid))
    if pmcid:
        identifiers.append(Identifier("pmcid", pmcid))

    primary_location = item.get("primary_location")
    primary = primary_location if isinstance(primary_location, Mapping) else {}
    source = primary.get("source")
    source = source if isinstance(source, Mapping) else {}
    journal = _clean_text(source.get("display_name"))

    url = _clean_text(primary.get("landing_page_url"))
    if not _is_http_url(url):
        url = f"https://doi.org/{doi}" if doi else f"https://openalex.org/{source_id}"

    return ProviderRecord(
        source_id=source_id,
        title=title,
        source_database="OpenAlex",
        response_index=response_index,
        identifiers=tuple(identifiers),
        authors=_authors(item.get("authorships")),
        journal_name=journal or None,
        publication_date=_publication_date(
            item.get("publication_date"), item.get("publication_year")
        ),
        abstract=_reconstruct_abstract(item.get("abstract_inverted_index")),
        url=url,
        keywords=_keywords(item),
        citation_count=_optional_nonnegative_count(
            item.get("cited_by_count"), "cited_by_count"
        ),
        publication_types=publication_types,
        publication_status="retracted" if raw_retracted is True else None,
    )


def _optional_nonnegative_count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProviderResponseError(f"OpenAlex {field} must be a non-negative integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdecimal():
        result = int(value.strip())
    else:
        raise ProviderResponseError(f"OpenAlex {field} must be a non-negative integer")
    if result < 0:
        raise ProviderResponseError(f"OpenAlex {field} must be a non-negative integer")
    return result


def _normalize_work_id(value: str) -> str:
    value = value.strip()
    if value.lower().startswith(("https://openalex.org/", "http://openalex.org/")):
        value = value.rstrip("/").rsplit("/", maxsplit=1)[-1]
    if not _WORK_ID_RE.fullmatch(value):
        raise ValueError("OpenAlex source_id must be a W-prefixed work ID")
    return value.upper()


def _normalize_doi(value: Any) -> str:
    doi = _clean_text(value)
    doi = _DOI_PREFIX_RE.sub("", doi)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE).strip()
    return doi or ""


def _normalize_pmid(value: Any) -> str:
    raw = _clean_text(value)
    if raw.isdigit():
        return raw
    if raw.casefold().startswith("pmid:"):
        candidate = raw.split(":", maxsplit=1)[1].strip()
        return candidate if candidate.isdigit() else ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.hostname and parsed.hostname.casefold() in {
        "pubmed.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"
    }:
        match = _PMID_RE.search(parsed.path)
        return match.group(1) if match else ""
    return ""


def _normalize_pmcid(value: Any) -> str:
    raw = _clean_text(value)
    if raw.casefold().startswith(("pmcid:", "pmc:")):
        raw = raw.split(":", maxsplit=1)[1].strip()
    if raw[:3].casefold() == "pmc" and raw[3:].isdigit():
        return raw.upper()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.hostname not in {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
        return ""
    match = _PMCID_RE.search(parsed.path)
    return match.group(1).upper() if match else ""


def _publication_date(value: Any, year_value: Any) -> PartialDate | None:
    raw_date = _clean_text(value)
    if raw_date:
        try:
            parts = raw_date.split("-", maxsplit=2)
            if len(parts) == 3:
                return PartialDate(int(parts[0]), int(parts[1]), int(parts[2]))
            if len(parts) == 2:
                return PartialDate(int(parts[0]), int(parts[1]))
            return PartialDate(int(parts[0]))
        except (ValueError, TypeError):
            pass
    try:
        return PartialDate(int(year_value)) if year_value is not None else None
    except (ValueError, TypeError):
        return None


def _reconstruct_abstract(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    words: dict[int, str] = {}
    try:
        for token, positions in value.items():
            if not isinstance(token, str) or not isinstance(positions, list):
                return None
            for position in positions:
                if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                    return None
                previous = words.get(position)
                if previous is not None and previous != token:
                    return None
                words[position] = token
    except (TypeError, ValueError):
        return None
    if not words:
        return None
    return " ".join(words[position] for position in sorted(words))


def _authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    for authorship in value:
        if not isinstance(authorship, Mapping):
            continue
        author = authorship.get("author")
        if isinstance(author, Mapping):
            name = _clean_text(author.get("display_name"))
            if name:
                names.append(name)
    return tuple(names)


def _keywords(item: Mapping[str, Any]) -> tuple[str, ...]:
    result: list[str] = []
    for field in ("keywords", "topics", "mesh"):
        values = item.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, Mapping):
                label = _clean_text(value.get("display_name") or value.get("name"))
            else:
                label = _clean_text(value)
            if label:
                result.append(label)
    return tuple(dict.fromkeys(result))


def _openalex_language_code(value: str) -> str | None:
    language = value.strip().casefold()
    if language in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[language]
    if language in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[language]
    if re.fullmatch(r"[a-z]{2}", language):
        return language
    return None


def _normalize_type(value: str) -> str:
    return " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())


def _contains_unquoted_bracket_group(expression: str) -> bool:
    """Recognize field-like bracket groups outside quoted search phrases."""
    index = 0
    in_quote = False
    while index < len(expression):
        char = expression[index]
        if char == "\\" and index + 1 < len(expression):
            index += 2
            continue
        if char == '"':
            in_quote = not in_quote
        elif not in_quote and char == "[":
            closing = expression.find("]", index + 1)
            if closing != -1:
                content = expression[index + 1 : closing]
                if re.fullmatch(r"\s*[A-Za-z][A-Za-z0-9 /_-]*\s*", content):
                    return True
        index += 1
    return False


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _is_http_url(value: str) -> bool:
    try:
        return urlsplit(value).scheme in {"http", "https"} and bool(urlsplit(value).netloc)
    except ValueError:
        return False


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderResponseError("OpenAlex returned invalid JSON") from exc


def _parse_nonnegative_int(value: Any, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError(f"OpenAlex {field} must be an integer") from exc
    if result < 0:
        raise ProviderResponseError(f"OpenAlex {field} must be non-negative")
    return result
