"""Portable workflow preferences and their persistence boundary."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ApplicationSettings:
    discovery_sources: tuple[str, ...] = ("pubmed", "europe_pmc")
    monitoring_sources: tuple[str, ...] = ("pubmed", "europe_pmc")
    page_size: int = 100
    max_pages: int = 5
    monitoring_interval_hours: int = 1
    export_include_excluded: bool = True
    contact_email: str = ""

    def __post_init__(self) -> None:
        for sources in (self.discovery_sources, self.monitoring_sources):
            if not isinstance(sources, tuple) or any(not isinstance(s, str) or not s for s in sources):
                raise ValueError("sources must be a tuple of non-empty names")
            if len(sources) != len(set(sources)):
                raise ValueError("duplicate source selections")
        if not self.discovery_sources:
            raise ValueError("select at least one default discovery source")
        for name, lower, upper in (("page_size", 1, 100), ("max_pages", 1, 100),
                                    ("monitoring_interval_hours", 1, 720)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be between {lower} and {upper}")
        if type(self.export_include_excluded) is not bool:
            raise ValueError("export_include_excluded must be boolean")
        if not isinstance(self.contact_email, str) or (
            self.contact_email and (self.contact_email.count("@") != 1
                                   or not all(self.contact_email.split("@"))
                                   or any(c.isspace() for c in self.contact_email))
        ):
            raise ValueError("contact email must be empty or a valid email address")


class SettingsStore(Protocol):
    def load(self) -> ApplicationSettings: ...
    def save(self, settings: ApplicationSettings) -> None: ...
