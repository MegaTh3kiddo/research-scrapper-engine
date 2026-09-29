"""Manual provider search outside the Qt UI thread."""

from __future__ import annotations

import asyncio

from PySide6.QtCore import QThread, Signal

from research_engine.application.discovery import PaginationError, UnsupportedFiltersError
from research_engine.desktop.bootstrap import DesktopServices
from research_engine.infrastructure.providers.http import (
    ProviderLimitError, ProviderRequestError, ProviderResponseError,
)


class SearchWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)
    source_started = Signal(str)
    source_finished = Signal(str, str)

    def __init__(
        self, services: DesktopServices, query_id: str, provider_name: str | tuple[str, ...],
        *, max_pages: int | None = None,
    ) -> None:
        super().__init__()
        self._services = services
        self._query_id = query_id
        self._provider_name = provider_name
        self._max_pages = max_pages

    def run(self) -> None:
        async def execute():
            service, providers = self._services.search_service()
            try:
                if isinstance(self._provider_name, str):
                    self.source_started.emit(self._provider_name)
                    try:
                        result = await service.run_query(
                            self._query_id, self._provider_name, max_pages=self._max_pages,
                        )
                    except Exception:
                        self.source_finished.emit(self._provider_name, "failed")
                        raise
                    self.source_finished.emit(self._provider_name, result.state)
                    return result
                selected = service.selected_sources(self._provider_name)
                self.source_started.emit(", ".join(selected))
                return await service.run_sources(self._query_id, self._provider_name,
                                                 max_pages=self._max_pages)
            finally:
                await asyncio.gather(*(provider.aclose() for provider in providers))

        try:
            result = asyncio.run(execute())
        except Exception as exc:
            # Only known adapters' sanitized messages may reach the desktop.
            self.failed.emit(_safe_message(exc))
        else:
            self.succeeded.emit(result)


class RetryWorker(QThread):
    """Resume an audited manual search without blocking the desktop thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, services: DesktopServices, run_id: str) -> None:
        super().__init__()
        self._services = services
        self._run_id = run_id

    def run(self) -> None:
        async def execute():
            service, providers = self._services.search_service()
            try:
                return await service.retry_run(self._run_id)
            finally:
                await asyncio.gather(*(provider.aclose() for provider in providers))

        try:
            result = asyncio.run(execute())
        except Exception as exc:
            self.failed.emit(_safe_message(exc))
        else:
            self.succeeded.emit(result)


def _safe_message(exc: Exception) -> str:
    safe = (ValueError, UnsupportedFiltersError, PaginationError,
            ProviderLimitError, ProviderRequestError, ProviderResponseError)
    return str(exc) if isinstance(exc, safe) else f"Search failed ({type(exc).__name__})"
