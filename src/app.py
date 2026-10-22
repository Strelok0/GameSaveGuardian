"""
Application entry and lifecycle initialization for Game Save Guardian.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from src.config import ConfigManager
from src.database import DatabaseManager
from src.ui.main_window import MainWindow
from src.utils.logging_config import setup_logging
from src.utils.paths import get_app_data_dir, get_app_icon_path


def create_app_window(
    minimized: bool = False,
    config_mgr: Optional[ConfigManager] = None,
    db_mgr: Optional[DatabaseManager] = None
) -> tuple[QApplication, MainWindow]:
    """Initialize application context and main window without blocking execution."""
    get_app_data_dir()

    if config_mgr is None:
        config_mgr = ConfigManager()
    settings = config_mgr.get_settings()

    setup_logging(verbose=settings.verbose_logging)

    if db_mgr is None:
        db_mgr = DatabaseManager()

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
        app.setApplicationName("GameSaveGuardian")
        app.setOrganizationName("GameSaveGuardian")
        app.setQuitOnLastWindowClosed(False)

    icon_p = get_app_icon_path("icon.ico")
    if icon_p.is_file():
        app.setWindowIcon(QIcon(str(icon_p)))

    window = MainWindow(config_manager=config_mgr, db_manager=db_mgr)
    if not minimized:
        window.show()
    else:
        window.hide()
        try:
            import ctypes
            ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
        except Exception:
            pass

    return app, window


def run_app(minimized: bool = False) -> int:
    """Launch the Game Save Guardian desktop application."""
    app, window = create_app_window(minimized=minimized)
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_app())
