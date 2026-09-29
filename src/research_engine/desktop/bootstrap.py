"""Compatibility client boundary; service composition is UI-independent."""

from research_engine.infrastructure.bootstrap import (
    ApplicationServices as DesktopServices,
    create_application_services as create_desktop_services,
    find_alembic_config,
)

__all__ = ["DesktopServices", "create_desktop_services", "find_alembic_config"]
