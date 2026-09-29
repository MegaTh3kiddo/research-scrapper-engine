"""Project-scoped CSV and RIS exports without a UI dependency."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from io import StringIO
from typing import Literal
from uuid import UUID

from research_engine.application.projects import ProjectService
from research_engine.ports.projects import PaperDetail


ExportFormat = Literal["csv", "ris"]


@dataclass(frozen=True, slots=True)
class ExportDocument:
    filename: str
    media_type: str
    content: bytes
    paper_count: int


class ExportService:
    def __init__(self, projects: ProjectService) -> None:
        self._projects = projects

    def export_project(
        self, project_id: UUID | str, format: ExportFormat, *,
        include_excluded: bool = True,
    ) -> ExportDocument:
        if format not in ("csv", "ris"):
            raise ValueError("export format must be csv or ris")
        project = self._projects.get_project(project_id)
        if project is None:
            raise ValueError("project does not exist")
        papers = self._projects.list_papers(
            project.id, include_excluded=include_excluded,
        )
        details = tuple(
            self._projects.get_paper_detail(project.id, paper.article_id)
            for paper in papers
        )
        if any(detail is None for detail in details):
            raise ValueError("project paper disappeared during export")
        if format == "csv":
            content = _csv(project.name, str(project.id), details)
            media_type = "text/csv; charset=utf-8"
        else:
            content = _ris(project.name, str(project.id), details)
            media_type = "application/x-research-info-systems; charset=utf-8"
        return ExportDocument(
            filename=f"research-project-{project.id}.{format}",
            media_type=media_type, content=content.encode("utf-8"),
            paper_count=len(details),
        )


_CSV_FIELDS = (
    "project_id", "project_name", "article_id", "title", "authors", "journal",
    "publication_date", "doi", "pmid", "url", "review_status", "is_read",
    "tags", "sources", "abstract", "publication_types", "publication_status",
    "source_databases", "citation_observations",
)


def _date(detail: PaperDetail) -> str:
    value = detail.publication_date
    if value is None:
        return ""
    result = str(value.year).zfill(4)
    if value.month is not None:
        result += f"-{value.month:02d}"
    if value.day is not None:
        result += f"-{value.day:02d}"
    return result


def _identifier(detail: PaperDetail, scheme: str) -> str:
    return next(
        (identifier.value for identifier in detail.identifiers
         if identifier.scheme.casefold() == scheme),
        "",
    )


def _csv_safe(value: str) -> str:
    # Spreadsheet software may evaluate formulas even in quoted CSV fields.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _csv(project_name: str, project_id: str, details: tuple[PaperDetail, ...]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(_CSV_FIELDS)
    for detail in details:
        paper = detail.project_paper
        values = (
            project_id, project_name, str(detail.article_id), detail.title,
            "; ".join(detail.authors), detail.journal or "", _date(detail),
            _identifier(detail, "doi"), _identifier(detail, "pmid"), detail.url or "",
            paper.status.value, "yes" if paper.is_read else "no",
            "; ".join(paper.tags), "; ".join(detail.source_providers),
            detail.abstract or "",
            "; ".join(detail.publication_types), detail.publication_status or "",
            "; ".join(sorted({source.source_database for source in detail.source_metadata
                              if source.source_database})),
            json.dumps([{"provider": item.provider, "count": item.count,
                         "as_of": item.as_of.isoformat(), "snapshot_id": item.snapshot_id}
                        for item in detail.citations], ensure_ascii=False),
        )
        writer.writerow(tuple(_csv_safe(value) for value in values))
    return output.getvalue()


def _ris_safe(value: str) -> str:
    return " ".join(value.split())


def _ris(project_name: str, project_id: str, details: tuple[PaperDetail, ...]) -> str:
    lines: list[str] = []
    for detail in details:
        paper = detail.project_paper
        def field(tag: str, value: str | None) -> None:
            if value:
                lines.append(f"{tag}  - {_ris_safe(value)}")

        field("TY", "JOUR" if detail.journal else "GEN")
        field("TI", detail.title)
        for author in detail.authors:
            field("AU", author)
        field("JO", detail.journal)
        published = _date(detail)
        field("PY", published[:4] if published else None)
        if len(published) > 4:
            field("DA", published)
        field("DO", _identifier(detail, "doi"))
        pmid = _identifier(detail, "pmid")
        field("AN", f"PMID:{pmid}" if pmid else None)
        field("UR", detail.url)
        field("AB", detail.abstract)
        for publication_type in detail.publication_types:
            field("N1", f"Publication type: {publication_type}")
        if detail.publication_status:
            field("N1", f"Publication status: {detail.publication_status}")
        for source in detail.source_metadata:
            if source.source_database:
                field("N1", f"Source database: {source.provider}/{source.source_database}")
        for item in detail.citations:
            field("N1", f"Citation count: {item.provider}={item.count}; as of {item.as_of.isoformat()}")
        field("N1", f"Research project: {project_name} ({project_id})")
        field("N1", f"Review status: {paper.status.value}")
        field("N1", f"Read: {'yes' if paper.is_read else 'no'}")
        for tag in paper.tags:
            field("N1", f"Project tag: {tag}")
        lines.append("ER  - ")
        lines.append("")
    return "\r\n".join(lines) + ("\r\n" if lines else "")
