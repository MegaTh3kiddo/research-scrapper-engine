"""JSON-safe normalized source assertions retained alongside immutable evidence."""

from research_engine.ports.providers import ProviderRecord


def source_assertions(record: ProviderRecord) -> dict:
    date = record.publication_date
    return {
        "schema_version": 1,
        "title": record.title,
        "source_database": record.source_database,
        "url": record.url,
        "authors": list(record.authors),
        "journal": record.journal_name,
        "publication_date": ({"year": date.year, "month": date.month, "day": date.day}
                             if date is not None else None),
        "abstract": record.abstract,
        "keywords": list(record.keywords),
        "identifiers": [{"scheme": item.scheme, "value": item.value}
                        for item in record.identifiers],
        "citation_count": record.citation_count,
        "publication_types": list(record.publication_types),
        "publication_status": record.publication_status,
    }
