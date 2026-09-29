"""Pure monitoring campaign planning; no provider or database implementation."""

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json

from research_engine.ports.ingestion import CoverageCheckpoint, CoverageContext, LoadedQuery
from research_engine.ports.providers import ProviderQueryPlan, SearchRequest


class NoRecentWindow(ValueError):
    """The saved query's date range does not intersect the recent window."""


def plan_coverage(loaded: LoadedQuery, original_plan: ProviderQueryPlan,
                  parser_version: str, lane: str, previous: CoverageCheckpoint | None,
                  now: datetime) -> tuple[SearchRequest, CoverageContext]:
    if lane not in {"full", "recent"}:
        raise ValueError("unknown monitoring coverage lane")
    signature = hashlib.sha256(json.dumps({
        "revision": loaded.revision_id, "provider": original_plan.provider,
        "translation": original_plan.translation_version, "parser": parser_version,
        "parameters": dict(original_plan.parameters), "query": original_plan.translated_query,
        "page_size": loaded.request.page_size,
    }, sort_keys=True).encode()).hexdigest()
    same = previous is not None and previous.revision_id == loaded.revision_id and previous.signature == signature
    resume = same and not previous.complete
    filters = loaded.request.filters
    lower, upper = filters.published_from, filters.published_to
    if lane == "recent":
        if resume:
            lower, upper = previous.published_from, previous.published_to
        else:
            upper = min(filters.published_to, now.date()) if filters.published_to else now.date()
            lower = previous.published_to - timedelta(days=7) if same and previous.complete and previous.published_to else now.date() - timedelta(days=30)
            if filters.published_from:
                lower = max(lower, filters.published_from)
            if lower > upper:
                raise NoRecentWindow("query has no recent publication window")
        request = replace(loaded.request, filters=replace(filters, published_from=lower, published_to=upper))
    else:
        request = loaded.request
    return request, CoverageContext(
        lane, signature, previous.cursor if resume else None, lower, upper,
        previous.id if previous else None,
    )
