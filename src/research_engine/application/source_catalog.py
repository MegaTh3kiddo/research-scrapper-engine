"""Source presentation metadata; providers still own executable capabilities."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    name: str
    label: str
    description: str
    api_key_env: str | None = None


LITERATURE_SOURCES = (
    SourceDescriptor("pubmed", "PubMed", "Biomedical index; Boolean queries, dates, language, publication type and humans filter.", "NCBI_API_KEY"),
    SourceDescriptor("europe_pmc", "Europe PMC", "Biomedical articles/preprints; Boolean queries, paired date bounds and open-access filters."),
    SourceDescriptor("openalex", "OpenAlex", "Multidisciplinary discovery; broader coverage. Source-specific query/filter capabilities apply.", "OPENALEX_API_KEY"),
    SourceDescriptor("crossref", "Crossref", "DOI metadata and dates; Boolean searches evaluate available titles, abstracts and subjects."),
)


def source_label(name: str) -> str:
    return next((source.label for source in LITERATURE_SOURCES if source.name == name), name)
