"""Crossref Boolean pagination through the real service and persistence stack."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from research_engine.infrastructure import bootstrap
from research_engine.infrastructure.persistence import models as m
from research_engine.infrastructure.snapshots import LocalSnapshotStorage
from research_engine.ports.settings import ApplicationSettings
from research_engine.ports.snapshots import StoredSnapshot


def _payload(request, items, next_cursor=None):
    body = json.dumps({
        "message": {
            "total-results": len(items),
            "next-cursor": next_cursor,
            "items": items,
        }
    }).encode()
    return httpx.Response(
        200,
        request=request,
        headers={"content-type": "application/json"},
        content=body,
    )


def _work(title):
    return {
        "DOI": "10.5555/Shared.Boolean.Paper",
        "title": [title],
        "author": [{"given": "Ada", "family": "Example"}],
        "abstract": "alpha beta gamma abstract",
        "subject": ["alpha", "beta", "gamma"],
    }


def test_crossref_boolean_ingestion_resumes_branches_and_deduplicates_provenance(
    tmp_path, monkeypatch
):
    calls = []

    def handler(request):
        query = request.url.params.get("query")
        cursor = request.url.params["cursor"]
        calls.append((query, cursor))
        if (query, cursor) == ("alpha", "*"):
            # This is a full raw candidate page, but it fails the full Boolean
            # expression and therefore contributes no canonical record.
            return _payload(request, [{
                "DOI": "10.5555/Filtered.Out",
                "title": ["Alpha only"],
            }], "alpha-resume")
        if (query, cursor) == ("alpha", "alpha-resume"):
            return _payload(request, [])
        if (query, cursor) in {("beta", "*"), ("gamma", "*")}:
            return _payload(request, [_work("Alpha beta gamma paper")])
        pytest.fail(f"unexpected Crossref request {query!r} cursor {cursor!r}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider_clock = iter(
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=i)
        for i in range(10)
    )
    provider = bootstrap.CrossrefProvider(
        client=client, min_interval=0, clock=lambda: next(provider_clock)
    )
    monkeypatch.setattr(bootstrap, "_provider_instances", lambda *_args: (provider,))

    services = bootstrap.create_application_services(tmp_path)
    services.settings.update(ApplicationSettings(page_size=1, max_pages=1))
    project = services.projects.create_project("Boolean branch fixture")
    query = services.projects.create_query(
        project.id,
        "alpha AND beta AND gamma",
        enabled_providers=("crossref",),
    )
    search, providers = services.search_service()

    async def run_and_close():
        try:
            first = await search.run_query(
                str(query.id), "crossref", page_size=1, max_pages=1
            )
            resumed = await search.retry_run(first.run_id, max_pages=3)
            return first, resumed
        finally:
            for item in providers:
                await item.aclose()

    try:
        first, resumed = asyncio.run(run_and_close())
        assert first.state == "partial"
        assert first.pages == 1
        assert first.outcome.observed == 0
        assert first.next_cursor.startswith("boolean-v1:")
        assert resumed.state == "completed"
        assert resumed.pages == 3
        assert resumed.outcome.observed == 1
        assert resumed.outcome.newly_discovered == 1
        assert calls == [
            ("alpha", "*"),
            ("alpha", "alpha-resume"),
            ("beta", "*"),
            ("gamma", "*"),
        ]

        with services.session_factory() as session:
            first_run = session.get(m.SearchRun, first.run_id)
            retry_run = session.get(m.SearchRun, resumed.run_id)
            assert first_run.state == "partial"
            assert first_run.cursor == first.next_cursor
            assert retry_run.state == "completed"

            snapshots = session.scalars(
                select(m.SourceSnapshot)
                .where(m.SourceSnapshot.search_run_id.in_((first.run_id, resumed.run_id)))
                .order_by(m.SourceSnapshot.captured_at, m.SourceSnapshot.id)
            ).all()
            assert len(snapshots) == 4
            first_snapshot = next(
                snapshot for snapshot in snapshots
                if snapshot.search_run_id == first.run_id
            )
            assert first_snapshot.request_metadata["candidate_branch"] == "0"
            assert first_snapshot.request_metadata["query"] == "alpha"
            assert first_snapshot.request_metadata["boolean_expression"] == (
                "alpha AND beta AND gamma"
            )
            assert first_snapshot.request_metadata["normalized_expression"] == (
                "(alpha AND beta) AND gamma"
            )
            stored_first = StoredSnapshot(
                storage_key=first_snapshot.storage_key,
                sha256=first_snapshot.sha256,
                compression=first_snapshot.compression,
                byte_size=first_snapshot.byte_size,
            )
            assert json.loads(
                LocalSnapshotStorage(services.snapshot_directory).load(stored_first)
            )["message"]["items"][0]["title"] == ["Alpha only"]

            articles = session.scalars(select(m.Article)).all()
            sources = session.scalars(select(m.SourceRecord)).all()
            identifiers = session.scalars(select(m.ArticleIdentifier)).all()
            observations = session.scalars(
                select(m.SearchRunObservation).where(
                    m.SearchRunObservation.run_id == resumed.run_id
                )
            ).all()
            events = session.scalars(
                select(m.OutboxEvent).where(m.OutboxEvent.type == "PaperDiscovered")
            ).all()
            source_links = session.scalars(select(m.SourceRecordSnapshot)).all()

            assert len(articles) == 1
            assert len(sources) == 1
            assert sources[0].provider_record_id == "10.5555/Shared.Boolean.Paper"
            assert {
                (identifier.scheme, identifier.value) for identifier in identifiers
            } == {
                ("doi", "10.5555/shared.boolean.paper"),
                ("provider:crossref:id", "10.5555/Shared.Boolean.Paper"),
            }
            assert len(observations) == 1
            assert observations[0].article_id == articles[0].id
            assert len(events) == 1
            assert events[0].aggregate_id == articles[0].id
            # Both candidate branches retain source snapshots linked to the
            # canonical Crossref record, while the first filtered page remains
            # available at run level without a record link.
            assert len(source_links) == 2
            assert {link.snapshot_id for link in source_links} <= {
                snapshot.id for snapshot in snapshots
            }
            assert first_snapshot.id not in {link.snapshot_id for link in source_links}
    finally:
        services.close()
        asyncio.run(client.aclose())
