"""UI-neutral syntax validation and provider translation preview; no network I/O."""

from dataclasses import dataclass

from research_engine.application.provider_registry import ProviderRegistry
from research_engine.domain.search_expression import parse_expression, render_expression
from research_engine.ports.providers import SearchRequest


@dataclass(frozen=True, slots=True)
class ProviderPreview:
    provider: str
    translated_query: str
    execution_mode: str
    notice: str | None = None


@dataclass(frozen=True, slots=True)
class QueryPreview:
    valid: bool
    normalized_expression: str
    providers: tuple[ProviderPreview, ...]


class QueryPreviewService:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    def preview(self, expression: str, providers: tuple[str, ...], *,
                allowed_sources: tuple[str, ...] | None = None) -> QueryPreview:
        tree = parse_expression(expression)
        previews = []
        for name in dict.fromkeys(providers):
            plan = self._registry.get(name).plan(SearchRequest(expression))
            filtered = "boolean_expression" in plan.parameters
            notice = None
            if filtered:
                notice = ("Crossref checks Boolean matches in available titles, abstracts and subjects. "
                          "Missing metadata can omit matches. Candidate pages and page budgets limit coverage.")
            elif name == "crossref":
                notice = "Crossref uses ranked keyword matching. Add Boolean operators or quotes to require metadata matches."
            if allowed_sources is not None and name not in allowed_sources:
                policy_notice = "This source is disabled for discovery in saved Settings. Enable it there before searching."
                notice = f"{notice} {policy_notice}" if notice else policy_notice
            previews.append(ProviderPreview(name, plan.translated_query,
                "metadata_filter" if filtered else "native", notice))
        return QueryPreview(True, render_expression(tree), tuple(previews))
