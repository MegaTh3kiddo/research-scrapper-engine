"""Desktop entry point."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from research_engine.desktop.bootstrap import create_desktop_services
from research_engine.application.local_monitor import LocalMonitoringRunner
from research_engine.desktop.controllers.main import MainController
from research_engine.desktop.views.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        services = create_desktop_services()
    except Exception as exc:
        QMessageBox.critical(
            None, "Research engine startup failed",
            f"Cannot open the research database ({type(exc).__name__}). "
            "Check the configured data directory and migrations.",
        )
        return 1
    window = MainWindow()
    controller = MainController(window, services)
    window._controller = controller  # keep controller alive for signal handlers
    monitor = LocalMonitoringRunner(services.monitoring)
    monitor.start()
    window.show()
    try:
        return app.exec()
    finally:
        monitor.stop()
        services.close()


if __name__ == "__main__":
    raise SystemExit(main())
