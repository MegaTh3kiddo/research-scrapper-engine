"""UI-neutral composition root for desktop, CLI and future server workers."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from research_engine.application.ingestion import IngestionService
from research_engine.application.identity_review import IdentityReviewService
from research_engine.application.exports import ExportService
from research_engine.application.monitoring import ScheduledMonitoringService
from research_engine.application.network_settings import NetworkSettingsService
from research_engine.application.projects import ProjectService
from research_engine.application.query_preview import QueryPreviewService
from research_engine.application.recovery import RecoveryService
from research_engine.application.provider_registry import ProviderRegistry
from research_engine.application.searches import ManualSearchService
from research_engine.application.settings import ApplicationSettingsService
from research_engine.application.sheets import SheetSyncService
from research_engine.infrastructure.persistence.database import create_database_engine
from research_engine.infrastructure.persistence.ingestion import SqlAlchemyIngestionStore
from research_engine.infrastructure.persistence.identity_review import SqlAlchemyIdentityReviewStore
from research_engine.infrastructure.persistence.projects import SqlAlchemyProjectStore
from research_engine.infrastructure.persistence.recovery import SqlAlchemyRecoveryStore
from research_engine.infrastructure.persistence.scheduling import SqlAlchemyScheduleStore
from research_engine.infrastructure.logging_config import configure_local_logging
from research_engine.infrastructure.network_settings import LocalNetworkSettingsStore
from research_engine.infrastructure.settings import LocalSettingsStore
from research_engine.infrastructure.google_sheets import GoogleSheetsGateway
from research_engine.infrastructure.persistence.sheets import SqlAlchemySheetLinkStore
from research_engine.infrastructure.providers.openalex import OpenAlexProvider
from research_engine.infrastructure.providers.crossref import CrossrefProvider
from research_engine.infrastructure.providers.europe_pmc import EuropePMCProvider
from research_engine.infrastructure.providers.pubmed import PubMedProvider
from research_engine.infrastructure.snapshots import LocalSnapshotStorage


@dataclass(slots=True)
class ApplicationServices:
    projects: ProjectService
    exports: ExportService
    monitoring: ScheduledMonitoringService
    recovery: RecoveryService
    identity_review: IdentityReviewService
    network_settings: NetworkSettingsService
    settings: ApplicationSettingsService
    sheets: SheetSyncService
    session_factory: sessionmaker
    snapshot_directory: Path
    engine: Engine

    def search_service(self, *, allowed_sources: tuple[str, ...] | None = None
                       ) -> tuple[ManualSearchService, tuple[object, ...]]:
        """Create providers in the worker thread that will run and close them."""
        providers = self.provider_instances()
        ingestion = IngestionService(
            ProviderRegistry(providers),
            SqlAlchemyIngestionStore(self.session_factory),
            LocalSnapshotStorage(self.snapshot_directory),
        )
        preferences = self.settings.get()
        return ManualSearchService(ingestion, page_size=preferences.page_size,
                                   max_pages=preferences.max_pages,
                                   allowed_sources=allowed_sources), providers

    def provider_instances(self) -> tuple[object, ...]:
        return _provider_instances(self.network_settings, self.settings)

    def query_preview_service(self) -> tuple[QueryPreviewService, tuple[object, ...]]:
        providers = self.provider_instances()
        return QueryPreviewService(ProviderRegistry(providers)), providers

    def close(self) -> None:
        self.engine.dispose()


def create_application_services(data_directory: Path | None = None) -> ApplicationServices:
    base = Path(data_directory) if data_directory else Path(
        os.environ.get("RESEARCH_ENGINE_DATA_DIR", Path.home() / ".research-scrapper-engine")
    )
    base.mkdir(parents=True, exist_ok=True)
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{base / 'research_engine.db'}")
    config_path = find_alembic_config()
    config = Config(str(config_path))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    configure_local_logging(base)
    network_settings = NetworkSettingsService(LocalNetworkSettingsStore(base))
    # Fail at startup if a saved network configuration is invalid or corrupt.
    network_settings.get()
    settings = ApplicationSettingsService(LocalSettingsStore(base))
    settings.get()
    engine = create_database_engine(database_url)
    session_factory = sessionmaker(engine, expire_on_commit=False)
    projects = ProjectService(SqlAlchemyProjectStore(session_factory))
    exports = ExportService(projects)
    sheets = SheetSyncService(
        SqlAlchemySheetLinkStore(session_factory), projects, exports,
        lambda: GoogleSheetsGateway(network_settings.get().active_proxy_url),
    )
    snapshot_directory = base / "snapshots"

    @asynccontextmanager
    async def scheduled_searches():
        providers = _provider_instances(network_settings, settings)
        preferences = settings.get()
        try:
            yield ManualSearchService(IngestionService(
                ProviderRegistry(providers),
                SqlAlchemyIngestionStore(session_factory),
                LocalSnapshotStorage(snapshot_directory),
            ), page_size=preferences.page_size, max_pages=preferences.max_pages,
                allowed_sources=preferences.monitoring_sources)
        finally:
            for provider in providers:
                await provider.aclose()

    return ApplicationServices(
        projects=projects,
        exports=exports,
        monitoring=ScheduledMonitoringService(
            SqlAlchemyScheduleStore(session_factory), scheduled_searches,
            on_completed=sheets.sync_scheduled_query,
        ),
        recovery=RecoveryService(SqlAlchemyRecoveryStore(session_factory)),
        identity_review=IdentityReviewService(SqlAlchemyIdentityReviewStore(session_factory)),
        network_settings=network_settings,
        settings=settings,
        sheets=sheets,
        session_factory=session_factory,
        snapshot_directory=snapshot_directory, engine=engine,
    )


def _provider_instances(
    network_settings: NetworkSettingsService,
    settings: ApplicationSettingsService,
) -> tuple[object, ...]:
    proxy_url = network_settings.get().active_proxy_url
    email = settings.get().contact_email or None
    pubmed_options = {"proxy_url": proxy_url}
    if email:
        pubmed_options["email"] = email
    if key := settings.api_key("pubmed"):
        pubmed_options["api_key"] = key
    return (
        PubMedProvider(**pubmed_options), EuropePMCProvider(proxy_url=proxy_url),
        OpenAlexProvider(proxy_url=proxy_url, api_key=settings.api_key("openalex")),
        CrossrefProvider(proxy_url=proxy_url, email=email),
    )


def find_alembic_config() -> Path:
    """Find migrations in an editable checkout or an installed wheel."""
    candidates = (
        Path(__file__).resolve().parents[3] / "alembic.ini",
        Path(sys.prefix) / "share" / "research-scrapper-engine" / "alembic.ini",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("Alembic configuration is unavailable in this installation")
