"""Crossref REST API adapter for publication discovery and DOI lookup."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import base64
import html
import json
import re
from typing import Any, NoReturn
from urllib.parse import quote, unquote, urlsplit

import httpx

from research_engine.domain.value_objects import Identifier, PartialDate
from research_engine.domain.search_expression import (
    Binary, Not, candidate_terms, matches_metadata, parse_expression, render_expression,
)
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


_DEFAULT_BASE_URL = "https://api.crossref.org/v1"
_TRANSLATION_VERSION = "crossref-boolean-v2"
_MAX_PAGE_SIZE = 1000
_HTML_TAG_RE = re.compile(r"<[^>]*>")


def _encode_boolean_cursor(branch: int, cursor: str | None) -> str:
    payload = json.dumps([branch, cursor], separators=(",", ":")).encode()
    return "boolean-v1:" + base64.urlsafe_b64encode(payload).decode()


def _decode_boolean_cursor(value: str, count: int) -> tuple[int, str | None]:
    try:
        if not value.startswith("boolean-v1:") or len(value) > 100_000:
            raise ValueError
        decoded = json.loads(base64.b64decode(value.split(":", 1)[1], altchars=b"-_", validate=True))
        if not isinstance(decoded, list) or len(decoded) != 2:
            raise ValueError
        branch, cursor = decoded
        if type(branch) is not int or not 0 <= branch < count or (
            cursor is not None and (not isinstance(cursor, str) or not cursor)
        ):
            raise ValueError
        return branch, cursor
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid Crossref Boolean cursor") from exc


class CrossrefProvider:
    """Query Crossref's Works API and normalize records to the shared contract.

    Crossref offers relevance based text search rather than the portable
    Boolean query syntax supported by some literature indexes. Boolean queries
    use paginated candidate searches followed by title/abstract/subject evaluation.
    Only publication date bounds map directly from :class:`SearchFilters`.

    The REST API has separate list and single-record rate pools. The common
    client has one limiter per provider, so this adapter conservatively uses
    the list limit for both operations: one request/second without ``mailto``
    and three requests/second with it. An injected client is still paced by
    this limiter. Close owned sessions with :meth:`aclose`.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | RateLimitedHTTPClient | None = None,
        *,
        email: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        min_interval: float | None = None,
        clock: Callable[[], datetime] | None = None,
        proxy_url: str | None = None,
    ) -> None:
        normalized_email = email.strip() if email and email.strip() else None
        if normalized_email and (
            "@" not in normalized_email or any(ch.isspace() for ch in normalized_email)
        ):
            raise ValueError("Crossref mailto must be a valid email address")
        self._email = normalized_email
        if isinstance(client, RateLimitedHTTPClient):
            if proxy_url is not None:
                raise ValueError("cannot configure a proxy with an injected HTTP client")
            self._http = client
        else:
            if min_interval is None:
                min_interval = 1.0 / 3.0 if self._email else 1.0
            self._http = RateLimitedHTTPClient(
                base_url,
                min_interval=min_interval,
                client=client,
                proxy_url=proxy_url,
            )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def name(self) -> str:
        return "crossref"

    @property
    def parser_version(self) -> str:
        return "crossref-parser-v3"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supported_filters=frozenset({"published_from", "published_to"}),
            supports_boolean_queries=True,
        )

    def plan(self, request: SearchRequest) -> ProviderQueryPlan:
        expression = parse_expression(request.expression)
        filters = request.filters
        unsupported: list[str] = []
        if filters.languages:
            unsupported.append("languages")
        if filters.publication_types:
            unsupported.append("publication_types")
        if filters.humans_only is not None:
            unsupported.append("humans_only")
        if filters.open_access_only is not None:
            unsupported.append("open_access_only")

        filter_values: list[str] = []
        if filters.published_from is not None:
            filter_values.append(f"from-pub-date:{filters.published_from.isoformat()}")
        if filters.published_to is not None:
            filter_values.append(f"until-pub-date:{filters.published_to.isoformat()}")

        params = {
            "query": request.expression.strip(),
            "rows": str(min(request.page_size, _MAX_PAGE_SIZE)),
        }
        if filter_values:
            params["filter"] = ",".join(filter_values)
        if isinstance(expression, (Binary, Not)) or expression.quoted:
            # Parse the original input on each candidate page. The fully grouped
            # display translation can exceed the input grammar's depth/token caps.
            params["boolean_expression"] = request.expression.strip()
            params["candidate_queries"] = json.dumps(candidate_terms(expression))
        return ProviderQueryPlan(
            provider=self.name,
            translated_query=render_expression(expression),
            translation_version=_TRANSLATION_VERSION,
            unsupported_filters=tuple(unsupported),
            parameters=params,
        )

    async def search(
        self, plan: ProviderQueryPlan, cursor: str | None = None
    ) -> SearchPage:
        self._validate_plan(plan)
        if plan.unsupported_filters:
            raise ValueError(
                "cannot execute Crossref query with unsupported features: "
                + ", ".join(plan.unsupported_filters)
            )
        params = dict(plan.parameters)
        boolean_expression = params.pop("boolean_expression", None)
        candidate_queries = json.loads(params.pop("candidate_queries", "[]"))
        branch = 0
        source_cursor = cursor
        if boolean_expression:
            if not candidate_queries or not all(isinstance(value, str) for value in candidate_queries):
                raise ValueError("invalid Crossref candidate queries")
            if cursor is not None:
                branch, source_cursor = _decode_boolean_cursor(cursor, len(candidate_queries))
            if candidate_queries[branch]:
                params["query"] = candidate_queries[branch]
            else:
                params.pop("query", None)
        params.setdefault("query", plan.translated_query)
        if boolean_expression and not candidate_queries[branch]:
            params.pop("query", None)
        params.setdefault("rows", "100")
        params["cursor"] = source_cursor or "*"
        try:
            rows = int(params["rows"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Crossref plan rows must be a positive integer") from exc
        if not 1 <= rows <= _MAX_PAGE_SIZE:
            raise ValueError("Crossref plan rows must be between 1 and 1000")
        self._add_email(params)

        response = await self._get("works", params)
        raw = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": "works",
                "query": params.get("query", ""),
                "cursor": params["cursor"],
                "rows": params["rows"],
                **({"filter": params["filter"]} if "filter" in params else {}),
                **({"boolean_expression": boolean_expression, "candidate_branch": str(branch),
                    "normalized_expression": plan.translated_query,
                    "execution_mode": "metadata_filter"} if boolean_expression else {}),
            },
        )
        message = _message(_parse_json(response), "search")
        items = message.get("items")
        if not isinstance(items, list):
            raise ProviderResponseError("Crossref search response has no items list")
        records = tuple(
            self._normalize_record(item, response_index=0)
            for item in items
            if isinstance(item, Mapping)
        )
        if len(records) != len(items):
            raise ProviderResponseError("Crossref returned a malformed work item")

        total_count = _parse_nonnegative_int(message.get("total-results"))
        next_cursor = message.get("next-cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ProviderResponseError("Crossref next-cursor must be a string")
        # Crossref documents a short page as the terminal condition. Preserve
        # any repeated cursor on a full page so the application pagination
        # guard can report an error instead of silently truncating results.
        if len(records) < rows or not next_cursor:
            next_cursor = None
        if boolean_expression:
            tree = parse_expression(boolean_expression)
            records = tuple(record for record in records if matches_metadata(
                tree, (record.title, record.abstract or "", *record.keywords)))
            if next_cursor is not None:
                if next_cursor == (source_cursor or "*"):
                    raise ProviderResponseError("Crossref repeated a candidate cursor")
                next_cursor = _encode_boolean_cursor(branch, next_cursor)
            elif branch + 1 < len(candidate_queries):
                next_cursor = _encode_boolean_cursor(branch + 1, None)
            # Candidate counts are not counts of Boolean matches or union totals.
            total_count = None
        return SearchPage(
            records=records,
            raw_responses=(raw,),
            next_cursor=next_cursor,
            total_count=total_count,
        )

    async def fetch(self, source_id: str) -> RecordResult:
        requested_doi = _normalize_doi(source_id, invalid_is_response=False)
        path_doi = quote(requested_doi, safe="")
        params: dict[str, str] = {}
        self._add_email(params)
        response = await self._get(f"works/{path_doi}", params)
        raw = self._raw_response(
            response,
            {
                "source": self.name,
                "endpoint": "works/{doi}",
                "doi": requested_doi,
            },
        )
        message = _message(_parse_json(response), "work")
        record = self._normalize_record(message, response_index=0)
        if _normalize_doi(record.source_id).casefold() != requested_doi.casefold():
            raise ProviderResponseError("Crossref returned a different DOI record")
        return RecordResult(record=record, raw_response=raw)

    async def aclose(self) -> None:
        """Close the underlying session if this provider owns it."""
        await self._http.aclose()

    def _add_email(self, params: dict[str, str]) -> None:
        if self._email:
            params["mailto"] = self._email

    async def _get(self, path: str, params: Mapping[str, str]) -> httpx.Response:
        try:
            return await self._http.get(path, params=params)
        except ProviderRequestError:
            raise
        except httpx.RequestError:
            raise ProviderRequestError("Crossref request failed") from None

    def _raw_response(
        self, response: httpx.Response, metadata: Mapping[str, str]
    ) -> RawResponse:
        fetched_at = self._clock()
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        media_type = response.headers.get("content-type", "application/json")
        media_type = media_type.split(";", maxsplit=1)[0].strip().lower()
        return RawResponse(
            body=bytes(response.content),
            media_type=media_type or "application/json",
            fetched_at=fetched_at,
            safe_request_metadata=dict(metadata),
        )

    @staticmethod
    def _validate_plan(plan: ProviderQueryPlan) -> None:
        if plan.provider != "crossref":
            raise ValueError(f"Crossref cannot execute provider plan {plan.provider!r}")

    @staticmethod
    def _normalize_record(item: Mapping[str, Any], *, response_index: int) -> ProviderRecord:
        doi = _normalize_doi(item.get("DOI"))
        title = _first_text(item.get("title"))
        if not doi:
            raise ProviderResponseError("Crossref work record must include a DOI")
        if not title:
            raise ProviderResponseError("Crossref work record must include a title")
        raw_type = item.get("type")
        if raw_type is not None and not isinstance(raw_type, str):
            raise ProviderResponseError("Crossref work type must be a string")
        publication_types = (raw_type.strip(),) if raw_type and raw_type.strip() else ()

        authors: list[str] = []
        author_items = item.get("author", [])
        if author_items is not None:
            if not isinstance(author_items, list):
                raise ProviderResponseError("Crossref author field must be a list")
            for author in author_items:
                if not isinstance(author, Mapping):
                    raise ProviderResponseError("Crossref author item must be an object")
                name = _clean_text(author.get("name"))
                if not name:
                    given = _clean_text(author.get("given"))
                    family = _clean_text(author.get("family"))
                    name = " ".join(part for part in (given, family) if part)
                if name:
                    authors.append(name)

        abstract = _clean_text(item.get("abstract")) or None
        return ProviderRecord(
            source_id=doi,
            title=title,
            source_database="crossref",
            response_index=response_index,
            identifiers=(Identifier("doi", doi),),
            # Equal display names can identify different people; preserve order/multiplicity.
            authors=tuple(authors),
            journal_name=_first_text(item.get("container-title")) or None,
            publication_date=_publication_date(item),
            abstract=abstract,
            url=f"https://doi.org/{quote(doi, safe='/()')}",
            keywords=_text_tuple(item.get("subject"), field="subject"),
            citation_count=_optional_nonnegative_count(
                item.get("is-referenced-by-count"), "is-referenced-by-count"
            ),
            publication_types=publication_types,
        )


def _message(payload: Any, response_kind: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderResponseError(f"Crossref {response_kind} response must be an object")
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise ProviderResponseError(f"Crossref {response_kind} response has no message object")
    return message


def _parse_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        raise ProviderResponseError("Crossref returned invalid JSON") from exc


def _parse_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError("Crossref total-results must be an integer") from exc
    if result < 0:
        raise ProviderResponseError("Crossref total-results must be non-negative")
    return result


def _optional_nonnegative_count(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProviderResponseError(f"Crossref {field} must be a non-negative integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdecimal():
        result = int(value.strip())
    else:
        raise ProviderResponseError(f"Crossref {field} must be a non-negative integer")
    if result < 0:
        raise ProviderResponseError(f"Crossref {field} must be a non-negative integer")
    return result


def _normalize_doi(value: Any, *, invalid_is_response: bool = True) -> str:
    def invalid() -> NoReturn:
        if invalid_is_response:
            raise ProviderResponseError("Crossref returned an invalid DOI")
        raise ValueError("source_id must be a DOI or DOI resolver URL without query or fragment")

    if value is None:
        return ""
    doi = _clean_text(value)
    if re.match(r"^https?://", doi, flags=re.IGNORECASE):
        try:
            parsed = urlsplit(doi)
            port = parsed.port
        except ValueError:
            invalid()
        host = (parsed.hostname or "").casefold()
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or host not in {"doi.org", "dx.doi.org"}
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            invalid()
        doi = unquote(parsed.path.lstrip("/"))
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE).strip()
    if not doi:
        return ""
    if not doi.casefold().startswith("10.") or "/" not in doi or any(ch.isspace() for ch in doi):
        invalid()
    return doi


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = _HTML_TAG_RE.sub(" ", text)
    return " ".join(text.split())


def _first_text(value: Any) -> str:
    candidates = value if isinstance(value, list) else [value]
    for candidate in candidates:
        if isinstance(candidate, str) and (text := _clean_text(candidate)):
            return text
    return ""


def _text_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProviderResponseError(f"Crossref {field} field must be a list")
    return tuple(dict.fromkeys(text for item in value if (text := _clean_text(item))))


def _publication_date(item: Mapping[str, Any]) -> PartialDate | None:
    for key in ("published-print", "published-online", "issued"):
        date_object = item.get(key)
        if not isinstance(date_object, Mapping):
            continue
        date_parts = date_object.get("date-parts")
        if not isinstance(date_parts, list) or not date_parts:
            continue
        first = date_parts[0]
        if not isinstance(first, list) or not first:
            continue
        try:
            year = int(first[0])
            month = int(first[1]) if len(first) > 1 else None
            day = int(first[2]) if len(first) > 2 else None
            return PartialDate(year=year, month=month, day=day)
        except (TypeError, ValueError, OverflowError):
            continue
    return None
