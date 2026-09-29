"""Atomic owner-only persistence for non-secret application preferences."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from research_engine.ports.settings import ApplicationSettings


class LocalSettingsStore:
    def __init__(self, directory: Path) -> None:
        self._path = Path(directory) / "application-settings.json"

    def load(self) -> ApplicationSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ApplicationSettings()
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("cannot read application settings") from None
        if not isinstance(data, dict) or set(data) != set(asdict(ApplicationSettings())):
            raise ValueError("invalid application settings file")
        for field in ("discovery_sources", "monitoring_sources"):
            if not isinstance(data[field], list):
                raise ValueError("source selections must be lists")
            data[field] = tuple(data[field])
        return ApplicationSettings(**data)

    def save(self, settings: ApplicationSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".application-settings-", dir=self._path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(asdict(settings), output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)
