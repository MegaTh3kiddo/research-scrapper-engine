"""UI-neutral manual search use case."""

from __future__ import annotations

from research_engine.application.ingestion import IngestionResult, IngestionService
from research_engine.application.discovery import UnsupportedFiltersError
from research_engine.application.coverage import NoRecentWindow


class ManualSearchService:
    def __init__(self, ingestion: IngestionService, *, page_size: int = 100,
                 max_pages: int = 1, allowed_sources: tuple[str, ...] | None = None) -> None:
        self._ingestion = ingestion
        self._page_size = page_size
        self._max_pages = max_pages
        self._allowed_sources = allowed_sources

    async def run_query(
        self, query_id: str, provider_name: str, *,
        page_size: int | None = None, max_pages: int | None = None,
        scheduled_execution_id: str | None = None,
    ) -> IngestionResult:
        if self._allowed_sources is not None and provider_name not in self._allowed_sources:
            self._ingestion.record_rejection(
                query_id, provider_name, "SourceDisabledForWorkflow",
            )
            raise ValueError("source is disabled for this workflow step")
        return await self._ingestion.run(
            query_id, provider_name, page_size=page_size if page_size is not None else self._page_size,
            max_pages=max_pages if max_pages is not None else self._max_pages,
            scheduled_execution_id=scheduled_execution_id,
        )

    async def retry_run(self, run_id: str, *, max_pages: int | None = None):
        return await self._ingestion.retry(run_id, page_size=self._page_size,
            max_pages=max_pages if max_pages is not None else self._max_pages)

    def selected_sources(self, sources: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(source for source in sources if self._allowed_sources is None
                     or source in self._allowed_sources)

    async def run_monitoring_query(self, query_id: str, provider_name: str, *,
                                   scheduled_execution_id: str, max_pages: int | None = None):
        if self._allowed_sources is not None and provider_name not in self._allowed_sources:
            self._ingestion.record_rejection(
                query_id, provider_name, "SourceDisabledForWorkflow",
            )
            raise ValueError("source is disabled for monitoring")
        try:
            supports_recent = self._ingestion.supports_recent(provider_name)
        except Exception as exc:
            self._ingestion.record_rejection(
                query_id, provider_name, type(exc).__name__,
            )
            raise
        lanes = ("full", "recent") if supports_recent else ("full",)
        results, errors = [], []
        for lane in lanes:
            try:
                results.append(await self._ingestion.run(
                    query_id, provider_name, page_size=self._page_size,
                    max_pages=max_pages if max_pages is not None else self._max_pages,
                    scheduled_execution_id=scheduled_execution_id, coverage_lane=lane,
                ))
            except NoRecentWindow:
                if lane != "recent":
                    raise
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]
        return tuple(results)

    async def run_sources(self, query_id: str, sources: tuple[str, ...], *,
                          max_pages: int | None = None):
        """Sequential batch: failed sources do not hide successful audited runs."""
        results = []
        for source in dict.fromkeys(sources):
            try:
                result = await self.run_query(query_id, source, max_pages=max_pages)
            except Exception as exc:
                # Exception text can contain secrets; expose codes only in batches.
                error = f"UnsupportedFiltersError: {exc}" if isinstance(exc, UnsupportedFiltersError) else type(exc).__name__
                results.append((source, None, error))
            else:
                results.append((source, result, None))
        return tuple(results)
