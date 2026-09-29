"""Local polling host for the UI-neutral scheduled monitoring use case."""

from __future__ import annotations

import asyncio
import logging
from threading import Event, Thread

from research_engine.application.monitoring import ScheduledMonitoringService


_LOG = logging.getLogger(__name__)


class LocalMonitoringRunner:
    def __init__(
        self, monitoring: ScheduledMonitoringService, *, poll_seconds: float = 60.0,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._monitoring = monitoring
        self._poll_seconds = poll_seconds
        self._stop = Event()
        self._thread: Thread | None = None
        self.last_error_code: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="research-monitor", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._monitoring.run_due_once())
            except Exception as exc:
                # No credentials or provider URLs in retained error state.
                self.last_error_code = type(exc).__name__
                _LOG.error("local_monitor_loop_failed error_code=%s", self.last_error_code)
            if self._stop.wait(self._poll_seconds):
                break
