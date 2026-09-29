"""Small, immutable values shared by the research engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PartialDate:
    """A publication date that may only be known to year or month precision."""

    year: int
    month: int | None = None
    day: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.year <= 9999:
            raise ValueError("year must be between 1 and 9999")
        if self.month is None:
            if self.day is not None:
                raise ValueError("day requires month")
            return
        if not 1 <= self.month <= 12:
            raise ValueError("month must be between 1 and 12")
        if self.day is not None:
            date(self.year, self.month, self.day)

    @property
    def precision(self) -> str:
        if self.day is not None:
            return "day"
        if self.month is not None:
            return "month"
        return "year"


@dataclass(frozen=True, slots=True)
class Identifier:
    """An identifier as provided by a source; normalization happens separately."""

    scheme: str
    value: str

    def __post_init__(self) -> None:
        if not self.scheme.strip() or not self.value.strip():
            raise ValueError("identifier scheme and value must be non-empty")


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Portable filters; each provider must disclose unsupported filters."""

    published_from: date | None = None
    published_to: date | None = None
    languages: tuple[str, ...] = ()
    publication_types: tuple[str, ...] = ()
    humans_only: bool | None = None
    open_access_only: bool | None = None

    def __post_init__(self) -> None:
        if (
            self.published_from is not None
            and self.published_to is not None
            and self.published_from > self.published_to
        ):
            raise ValueError("published_from must not follow published_to")
