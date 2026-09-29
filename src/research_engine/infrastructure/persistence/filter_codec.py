"""JSON representation of portable search filters."""

from __future__ import annotations

from datetime import date

from research_engine.domain.value_objects import SearchFilters


def encode_filters(filters: SearchFilters) -> dict[str, object]:
    return {
        "published_from": filters.published_from.isoformat() if filters.published_from else None,
        "published_to": filters.published_to.isoformat() if filters.published_to else None,
        "languages": list(filters.languages),
        "publication_types": list(filters.publication_types),
        "humans_only": filters.humans_only,
        "open_access_only": filters.open_access_only,
    }


def decode_filters(value: dict[str, object]) -> SearchFilters:
    if not isinstance(value, dict):
        raise ValueError("saved query filters must be an object")
    allowed = {
        "published_from", "published_to", "languages", "publication_types",
        "humans_only", "open_access_only",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown saved query filters: {', '.join(sorted(unknown))}")
    for name in ("humans_only", "open_access_only"):
        selected = value.get(name)
        if selected is not None and not isinstance(selected, bool):
            raise ValueError(f"saved query {name} must be Boolean or null")
    return SearchFilters(
        published_from=_date(value.get("published_from")),
        published_to=_date(value.get("published_to")),
        languages=_strings(value.get("languages"), "languages"),
        publication_types=_strings(value.get("publication_types"), "publication_types"),
        humans_only=value.get("humans_only"),
        open_access_only=value.get("open_access_only"),
    )


def _date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("saved query dates must be ISO strings")
    return date.fromisoformat(value)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"saved query {name} must be a list of non-empty strings")
    return tuple(value)
