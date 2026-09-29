"""Render and atomically save a project export off the UI thread."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QThread, Signal

from research_engine.application.exports import ExportFormat, ExportService


class ExportWorker(QThread):
    succeeded = Signal(int)
    failed = Signal(str)

    def __init__(
        self, exporter: ExportService, project_id: str,
        format: ExportFormat, destination: Path,
        *, include_excluded: bool = True,
    ) -> None:
        super().__init__()
        self._exporter = exporter
        self._project_id = project_id
        self._format = format
        self._destination = destination
        self._include_excluded = include_excluded

    def run(self) -> None:
        temporary: Path | None = None
        try:
            document = self._exporter.export_project(self._project_id, self._format,
                                                     include_excluded=self._include_excluded)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._destination.parent, prefix=".research-export-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(document.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._destination)
        except Exception as exc:
            self.failed.emit(f"Export failed ({type(exc).__name__}). Check the selected path.")
        else:
            self.succeeded.emit(document.paper_count)
        finally:
            if temporary is not None and temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
