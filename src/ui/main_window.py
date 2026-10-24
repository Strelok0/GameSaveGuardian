"""
Main application window for Game Save Guardian.
Provides sidebar navigation, system tray integration, and non-blocking background workers.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from src.backup.backup_manager import BackupManager
from src.backup.retention import check_and_compact_vault_if_needed
from src.backup.zip_writer import compact_vault, get_master_archive_filename
from src.config import ConfigManager
from src.database import DatabaseManager
from src.models import (
    AppSettings,
    ArchiveMode,
    BackupRecord,
    BackupStatus,
    BackupTrigger,
    GameProfile,
    ProtectionMode,
)
from src.monitoring.file_watcher import FileWatcher
from src.restore.restore_manager import AutoHealResult, RestoreManager
from src.ui.dashboard import DashboardPage
from src.ui.history_page import HistoryPage
from src.ui.restore_dialog import RestoreDialog
from src.ui.settings_page import SettingsPage
from src.ui.setup_wizard import SetupWizardDialog
from src.ui.styles import get_stylesheet
from src.utils.i18n import is_rtl, set_language, tr
from src.utils.logging_config import get_logger
from src.utils.paths import format_bytes, get_app_icon_path, get_disk_free_space, is_fullscreen_window_active

logger = get_logger()


class BackupWorkerThread(QThread):
    """Executes long-running backup compression and verification outside the UI thread."""

    progress_updated = Signal(int, int, str)
    finished = Signal(bool, str, object)

    def __init__(
        self,
        backup_manager: BackupManager,
        trigger: BackupTrigger = BackupTrigger.MANUAL,
        force: bool = False,
        game_profile: Optional[GameProfile] = None
    ):
        super().__init__()
        self.mgr = backup_manager
        self.trigger = trigger
        self.force = force
        self.game_profile = game_profile

    def run(self):
        def _prog(cur, tot, fpath):
            self.progress_updated.emit(cur, tot, fpath)

        ok, msg, record = self.mgr.execute_backup(
            trigger=self.trigger,
            force=self.force,
            progress_callback=_prog,
            game_profile=self.game_profile
        )
        self.finished.emit(ok, msg, record)


class AutoHealWorkerThread(QThread):
    """Executes save file verification and missing save restoration outside the UI thread."""

    progress_updated = Signal(int, int, str)
    finished = Signal(object)

    def __init__(
        self,
        restore_manager: RestoreManager,
        save_dir: str,
        backup_dir: str,
        game_name: str
    ):
        super().__init__()
        self.mgr = restore_manager
        self.save_dir = save_dir
        self.backup_dir = backup_dir
        self.game_name = game_name

    def run(self):
        def _prog(cur, tot, fpath):
            self.progress_updated.emit(cur, tot, fpath)

        res = self.mgr.check_and_restore_missing_saves(
            save_dir=self.save_dir,
            backup_path_or_dir=self.backup_dir,
            game_name=self.game_name,
            progress_callback=_prog
        )
        self.finished.emit(res)


class MainWindow(QMainWindow):
    """Main application window for Game Save Guardian."""

    realtime_change_signal = Signal(str)

    def __init__(self, config_manager: ConfigManager, db_manager: DatabaseManager):
        super().__init__()
        self.config_mgr = config_manager
        self.db = db_manager
        self.settings = self.config_mgr.get_settings()

        self.backup_mgr = BackupManager(self.db, self.settings)
        self.restore_mgr = RestoreManager(self.db)
        self.file_watcher: Optional[FileWatcher] = None
        self.worker_thread: Optional[BackupWorkerThread] = None
        self.auto_heal_thread: Optional[AutoHealWorkerThread] = None
        self._pending_backups: list[tuple[BackupTrigger, bool, Optional[GameProfile]]] = []

        self.protection_active = False
        self.protection_paused = False

        # Thread-safe GUI dispatch for realtime watcher events
        self.realtime_change_signal.connect(self._handle_realtime_change_on_gui_thread)

        # Scheduled safety timer
        self.sched_timer = QTimer(self)
        self.sched_timer.timeout.connect(self._on_scheduled_timer_tick)

        # Seconds countdown tracking
        self._next_scan_seconds = 0
        self.countdown_timer = QTimer(self)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)
        self.countdown_timer.start(1000)

        self._init_ui()
        self._init_tray()
        self._apply_theme()
        self._apply_language()

        # Startup self-healing for active game save directory
        if not self.settings.active_game.save_directory and "witcher" in self.settings.active_game.name.lower():
            from src.utils.paths import detect_witcher_3_save_paths
            w3_paths = detect_witcher_3_save_paths()
            if w3_paths:
                self.settings.active_game.save_directory = str(w3_paths[0])
                self.config_mgr.save_config(self.settings)
                logger.info("Startup self-healing restored Witcher 3 save path: %s", w3_paths[0])

        # Database startup reconciliation
        self.db.reconcile_backups(self.settings.active_game.name)

        # First run wizard check
        if not self.settings.first_run_completed:
            QTimer.singleShot(200, self._open_first_run_wizard)
        else:
            self._start_protection()

        self._refresh_metrics()

    def _init_ui(self) -> None:
        self.setWindowTitle(tr("app_title"))
        self.resize(1120, 700)
        self.setMinimumSize(920, 580)

        # Application Window Icon
        icon_path = get_app_icon_path("icon.ico")
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Sidebar Navigation
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(210)
        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(12, 20, 12, 20)
        sb_layout.setSpacing(8)

        # App Logo & Header
        app_logo_lbl = QLabel(tr("app_title"))
        app_logo_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #10b981; padding-left: 8px;")
        sb_layout.addWidget(app_logo_lbl)

        sub_lbl = QLabel("v1.0.0")
        sub_lbl.setStyleSheet("font-size: 11px; color: #64748b; padding-left: 8px; margin-bottom: 12px;")
        sb_layout.addWidget(sub_lbl)

        # Nav Buttons
        self.nav_group = QButtonGroup(self)
        self.btn_nav_dash = QPushButton(f"  {tr('nav_dashboard')}")
        self.btn_nav_dash.setProperty("class", "nav-btn")
        self.btn_nav_dash.setCheckable(True)
        self.btn_nav_dash.setChecked(True)
        self.nav_group.addButton(self.btn_nav_dash, 0)
        sb_layout.addWidget(self.btn_nav_dash)

        self.btn_nav_hist = QPushButton(f"  {tr('nav_history')}")
        self.btn_nav_hist.setProperty("class", "nav-btn")
        self.btn_nav_hist.setCheckable(True)
        self.nav_group.addButton(self.btn_nav_hist, 1)
        sb_layout.addWidget(self.btn_nav_hist)

        self.btn_nav_settings = QPushButton(f"  {tr('nav_settings')}")
        self.btn_nav_settings.setProperty("class", "nav-btn")
        self.btn_nav_settings.setCheckable(True)
        self.nav_group.addButton(self.btn_nav_settings, 2)
        sb_layout.addWidget(self.btn_nav_settings)

        sb_layout.addStretch()

        root_layout.addWidget(self.sidebar)

        # 2. Content Pages
        self.stack = QStackedWidget()
        self.page_dashboard = DashboardPage(self.settings, self)
        self.page_history = HistoryPage(self.db, self.restore_mgr, settings=self.settings, parent=self)
        self.page_settings = SettingsPage(self.config_mgr, self)

        self.stack.addWidget(self.page_dashboard)
        self.stack.addWidget(self.page_history)
        self.stack.addWidget(self.page_settings)

        root_layout.addWidget(self.stack, 1)

        # Connect Navigation
        self.nav_group.idClicked.connect(self._on_nav_page_changed)

        # Connect Dashboard Signals
        self.page_dashboard.request_start_protection.connect(self._start_protection)
        self.page_dashboard.request_pause_protection.connect(self._pause_protection)
        self.page_dashboard.request_resume_protection.connect(self._resume_protection)
        self.page_dashboard.request_backup_now.connect(lambda: self.trigger_backup(trigger=BackupTrigger.MANUAL, force=True))
        self.page_dashboard.request_open_restore.connect(self._on_dashboard_restore_requested)
        self.page_dashboard.request_auto_heal.connect(lambda: self.check_and_auto_heal_saves(notify_if_clean=True))
        self.page_dashboard.request_open_backup_folder.connect(self._open_backup_folder)
        self.page_dashboard.request_open_settings.connect(lambda: self._on_nav_page_changed(2))
        self.page_dashboard.request_switch_game.connect(self._on_switch_active_game)
        self.page_dashboard.request_compact_vault.connect(self._on_dashboard_compact_vault)
        self.page_dashboard.request_refresh_metrics.connect(self._refresh_metrics)

        # Connect History Signals
        self.page_history.request_restore_backup.connect(self._open_restore_dialog)
        self.page_history.request_manual_backup.connect(lambda: self.trigger_backup(trigger=BackupTrigger.MANUAL, force=True))
        self.page_history.request_switch_game.connect(self._on_switch_active_game)

        # Connect Settings Signals
        self.page_settings.settings_updated.connect(self._on_settings_updated)
        self.page_settings.theme_changed.connect(self._on_theme_changed)
        self.page_settings.language_changed.connect(self._on_language_changed)
        self.page_settings.request_switch_game.connect(self._on_switch_active_game)

    def _init_tray(self) -> None:
        """Set up system tray icon with context actions."""
        self.tray = QSystemTrayIcon(self)

        tray_icon_path = get_app_icon_path("tray_active.ico")
        if tray_icon_path.is_file():
            self.tray.setIcon(QIcon(str(tray_icon_path)))
        else:
            self.tray.setIcon(self.windowIcon())

        from PySide6.QtWidgets import QMenu
        tray_menu = QMenu()

        act_open = QAction(tr("tray_open"), self)
        act_open.triggered.connect(self._show_window)
        tray_menu.addAction(act_open)

        tray_menu.addSeparator()

        self.tray_act_pause = QAction(tr("tray_pause"), self)
        self.tray_act_pause.triggered.connect(self._pause_protection)
        tray_menu.addAction(self.tray_act_pause)

        self.tray_act_resume = QAction(tr("tray_resume"), self)
        self.tray_act_resume.triggered.connect(self._resume_protection)
        tray_menu.addAction(self.tray_act_resume)

        act_backup = QAction(tr("tray_backup_now"), self)
        act_backup.triggered.connect(lambda: self.trigger_backup(trigger=BackupTrigger.MANUAL, force=True))
        tray_menu.addAction(act_backup)

        act_folder = QAction(tr("tray_open_folder"), self)
        act_folder.triggered.connect(self._open_backup_folder)
        tray_menu.addAction(act_folder)

        tray_menu.addSeparator()

        act_exit = QAction(tr("tray_exit"), self)
        act_exit.triggered.connect(self._exit_app)
        tray_menu.addAction(act_exit)

        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_nav_page_changed(self, page_id: int) -> None:
        self.stack.setCurrentIndex(page_id)
        btn = self.nav_group.button(page_id)
        if btn and not btn.isChecked():
            btn.setChecked(True)

        if page_id == 1:
            self.page_history.set_active_game_filter(self.settings.active_game.name, sync_global=False)
        elif page_id == 2:
            self.page_settings.set_active_game_profile(self.settings.active_game.name, emit_switch=False)
        elif page_id == 0:
            self._refresh_metrics()

    def _start_protection(self) -> None:
        from src.utils.paths import KNOWN_GAME_PROCESSES

        # Collect save directories and processes for all enabled games
        watch_dirs: dict[str, Path] = {}
        processes: dict[str, str] = {}
        for g in self.settings.games:
            if g.enabled and g.save_directory:
                p = Path(g.save_directory)
                if p.is_dir():
                    watch_dirs[g.name] = p
                proc = g.process_name or KNOWN_GAME_PROCESSES.get(g.name.strip().lower())
                if proc:
                    processes[g.name] = proc

        if not watch_dirs:
            logger.warning("Cannot start protection: no valid game save directories configured.")
            self.protection_active = False
            self._refresh_metrics()
            return

        self.protection_active = True
        self.protection_paused = False

        # 1. Setup real-time file watcher if hybrid or realtime mode
        mode = self.settings.backup_mode
        if mode in (ProtectionMode.HYBRID.value, ProtectionMode.REALTIME.value):
            # If watcher is already running and watching the exact same dirs, avoid restart
            if (
                self.file_watcher
                and self.file_watcher.is_running()
                and hasattr(self.file_watcher, "watch_dirs")
                and set(self.file_watcher.watch_dirs.keys()) == set(watch_dirs.keys())
                and set(self.file_watcher.watch_dirs.values()) == set(watch_dirs.values())
            ):
                pass
            else:
                if self.file_watcher:
                    self.file_watcher.stop()

                self.file_watcher = FileWatcher(
                    watch_directories=watch_dirs,
                    on_change_callback=self._on_realtime_change_detected,
                    debounce_seconds=self.settings.debounce_seconds,
                    stability_checks=self.settings.stability_check_count,
                    processes=processes,
                    on_game_exit_callback=self._on_game_exited
                )
                self.file_watcher.start()

        # 2. Setup scheduled timer if hybrid or scheduled mode
        if mode in (ProtectionMode.HYBRID.value, ProtectionMode.SCHEDULED.value):
            interval_ms = self.settings.scheduled_interval_minutes * 60 * 1000
            self.sched_timer.start(interval_ms)
            self._next_scan_seconds = self.settings.scheduled_interval_minutes * 60
        else:
            self.sched_timer.stop()
            self._next_scan_seconds = 0

        # Perform initial backup if active game has no backup yet
        last_rec = self.db.get_latest_successful_backup(self.settings.active_game.name)
        if last_rec is None and self.settings.active_game.save_directory and Path(self.settings.active_game.save_directory).is_dir():
            self.trigger_backup(trigger=BackupTrigger.INITIAL, force=False)

        # Automated missing save files verification (Auto-Heal)
        if self.settings.auto_heal_missing_saves:
            QTimer.singleShot(1500, lambda: self.check_and_auto_heal_saves(notify_if_clean=False))

        self._refresh_metrics()

    def _pause_protection(self) -> None:
        self.protection_paused = True
        if self.file_watcher:
            self.file_watcher.pause()
        self.sched_timer.stop()
        paused_p = get_app_icon_path("tray_paused.ico")
        if paused_p.is_file():
            self.tray.setIcon(QIcon(str(paused_p)))
        self._refresh_metrics()

    def _resume_protection(self) -> None:
        self.protection_paused = False
        if self.file_watcher:
            self.file_watcher.resume()
        mode = self.settings.backup_mode
        if mode in (ProtectionMode.HYBRID.value, ProtectionMode.SCHEDULED.value):
            interval_ms = self.settings.scheduled_interval_minutes * 60 * 1000
            self.sched_timer.start(interval_ms)
            self._next_scan_seconds = self.settings.scheduled_interval_minutes * 60
        active_p = get_app_icon_path("tray_active.ico")
        if active_p.is_file():
            self.tray.setIcon(QIcon(str(active_p)))
        self._refresh_metrics()

    def _on_realtime_change_detected(self, game_name: Optional[str] = "default") -> None:
        if self.protection_active and not self.protection_paused:
            self.realtime_change_signal.emit(game_name or "default")

    def _handle_realtime_change_on_gui_thread(self, game_name: str) -> None:
        if not self.protection_active or self.protection_paused:
            return

        target_profile = None
        if game_name and game_name != "default":
            target_profile = next((g for g in self.settings.games if g.name == game_name), None)

        if target_profile is None:
            target_profile = self.settings.active_game

        logger.info(
            "Realtime filesystem change confirmed for '%s'. Scheduling backup snapshot.",
            target_profile.name
        )

        # Automatically switch active game in UI so the user sees the game they are currently playing
        if self.settings.active_game.name != target_profile.name:
            logger.info(
                "Active profile was '%s', but user is playing '%s'. Auto-switching active profile.",
                self.settings.active_game.name, target_profile.name
            )
            self._on_switch_active_game(target_profile.name)

        self.trigger_backup(trigger=BackupTrigger.REALTIME, force=False, game_profile=target_profile)

    def _on_scheduled_timer_tick(self) -> None:
        if self.protection_active and not self.protection_paused:
            logger.info("Scheduled backup scan timer fired.")
            self._next_scan_seconds = self.settings.scheduled_interval_minutes * 60
            self.trigger_backup(trigger=BackupTrigger.SCHEDULED, force=False)
            if self.settings.auto_heal_missing_saves:
                QTimer.singleShot(2000, lambda: self.check_and_auto_heal_saves(notify_if_clean=False))

    def _on_countdown_tick(self) -> None:
        if self.protection_active and not self.protection_paused and self._next_scan_seconds > 0:
            self._next_scan_seconds -= 1
            mins = self._next_scan_seconds // 60
            secs = self._next_scan_seconds % 60
            self.page_dashboard.val_next_scan.setText(f"{mins:02d}:{secs:02d}")
        else:
            if not self.protection_active:
                self.page_dashboard.val_next_scan.setText("Stopped")
            elif self.protection_paused:
                self.page_dashboard.val_next_scan.setText("Paused")

    def trigger_backup(
        self,
        trigger: BackupTrigger = BackupTrigger.MANUAL,
        force: bool = False,
        game_profile: Optional[GameProfile] = None
    ) -> None:
        """Initiate non-blocking backup operation in worker thread."""
        target_profile = game_profile or self.settings.active_game

        if self.worker_thread and self.worker_thread.isRunning():
            logger.info("Backup already in progress. Enqueuing backup for '%s'.", target_profile.name)
            if not any(p.name == target_profile.name for _, _, p in self._pending_backups if p):
                self._pending_backups.append((trigger, force, target_profile))
            return

        self.worker_thread = BackupWorkerThread(
            self.backup_mgr, trigger=trigger, force=force, game_profile=target_profile
        )
        self.worker_thread.finished.connect(self._on_backup_finished)
        self.worker_thread.start(QThread.LowPriority)

    def notify(self, title: str, message: str, is_critical: bool = False, is_manual: bool = False) -> bool:
        """
        Show tray balloon notification respecting user settings, fullscreen detection, and Silent Gaming Mode.
        Returns True if notification was emitted, False if suppressed.
        """
        if not self.settings.notifications_enabled:
            return False

        # Suppress routine automated notifications if silent gaming mode is on or game is in fullscreen
        if (self.settings.silent_gaming_mode or is_fullscreen_window_active()) and not is_manual and not is_critical:
            logger.info("Notification suppressed by Silent Gaming Mode / Fullscreen: %s", message)
            return False

        icon = QSystemTrayIcon.Warning if is_critical else QSystemTrayIcon.Information
        self.tray.showMessage(title, message, icon, 4000 if is_critical else 3000)
        return True

    def _on_backup_finished(self, success: bool, message: str, record: Optional[BackupRecord]) -> None:
        self._refresh_metrics()
        self.page_history.refresh_records()

        is_manual = (record is not None and record.trigger_type == BackupTrigger.MANUAL.value)
        game_title = record.game_name if record else self.settings.active_game.name
        if success and record:
            self.notify(
                "Game Save Guardian",
                f"[{game_title}] Backup completed successfully!\n{record.filename}",
                is_critical=False,
                is_manual=is_manual
            )
        elif not success and "No changes detected" not in message:
            logger.warning("Backup failed for %s: %s", game_title, message)
            self.notify(
                "Backup Alert",
                f"[{game_title}] Backup failed: {message}",
                is_critical=True,
                is_manual=is_manual
            )

        # Process any pending queued backups
        if self._pending_backups:
            next_trigger, next_force, next_prof = self._pending_backups.pop(0)
            self.trigger_backup(trigger=next_trigger, force=next_force, game_profile=next_prof)

    def _refresh_metrics(self) -> None:
        total_backups = len(self.db.get_all_backups(self.settings.active_game.name, status_filter=BackupStatus.SUCCESS.value))
        total_bytes = self.db.get_total_storage_used(self.settings.active_game.name)
        last_rec = self.db.get_latest_successful_backup(self.settings.active_game.name)

        mins = self._next_scan_seconds // 60
        secs = self._next_scan_seconds % 60
        next_str = f"{mins:02d}:{secs:02d}" if self._next_scan_seconds > 0 else "--"

        self.page_dashboard.update_metrics(
            protection_active=self.protection_active,
            protection_paused=self.protection_paused,
            total_backups=total_backups,
            total_bytes=total_bytes,
            last_backup_rec=last_rec,
            next_scan_text=next_str
        )

    def _open_restore_dialog(self, record: BackupRecord) -> None:
        dlg = RestoreDialog(
            record=record,
            restore_manager=self.restore_mgr,
            active_save_dir=self.settings.active_game.save_directory,
            backup_dir=str(self.settings.get_backup_dir_for_game(self.settings.active_game)),
            parent=self
        )
        dlg.restore_completed.connect(lambda p: self._refresh_metrics())
        dlg.exec()

    def _on_dashboard_restore_requested(self) -> None:
        latest = self.db.get_latest_successful_backup(self.settings.active_game.name)
        if latest:
            self._open_restore_dialog(latest)
        else:
            QMessageBox.information(self, "No Backups", "No successful backups available to restore.")

    def check_and_auto_heal_saves(
        self,
        game_profile: Optional[GameProfile] = None,
        notify_if_clean: bool = False
    ) -> None:
        """
        Verify the original game save folder against backup archives and automatically restore
        any missing save files without overwriting existing files.
        Safely pauses the file watcher during restoration to prevent re-trigger loops.
        """
        if not self.settings.auto_heal_missing_saves and not notify_if_clean:
            return

        profile = game_profile or self.settings.active_game
        if not profile or not profile.save_directory:
            if notify_if_clean:
                QMessageBox.warning(self, "Auto-Heal", "No valid save directory configured for this game.")
            return

        save_p = Path(profile.save_directory)
        if not save_p.is_dir():
            if notify_if_clean:
                QMessageBox.warning(self, "Auto-Heal", f"Save directory not found: {save_p}")
            return

        backup_dir = str(self.settings.get_backup_dir_for_game(profile))

        # Check if process is actively running: defer automated healing to avoid interfering with game process
        from src.monitoring.file_watcher import check_is_process_running
        proc_name = profile.process_name
        if not proc_name:
            from src.utils.paths import KNOWN_GAME_PROCESSES
            proc_name = KNOWN_GAME_PROCESSES.get(profile.name.strip().lower())

        if proc_name and check_is_process_running(proc_name) and not notify_if_clean:
            logger.info("Monitored game '%s' is actively running. Deferring auto-heal until game exits.", profile.name)
            return

        if self.auto_heal_thread and self.auto_heal_thread.isRunning():
            logger.info("Auto-heal check already in progress for %s. Skipping duplicate request.", profile.name)
            return

        # Temporarily pause file watcher to avoid reacting to our own restored save files
        watcher_was_active = bool(self.file_watcher and self.file_watcher.is_running() and not self.file_watcher.is_paused())
        if watcher_was_active and self.file_watcher:
            self.file_watcher.pause()

        self.auto_heal_thread = AutoHealWorkerThread(
            restore_manager=self.restore_mgr,
            save_dir=str(save_p),
            backup_dir=backup_dir,
            game_name=profile.name
        )

        def _on_finished(result: AutoHealResult):
            # Give filesystem events 500ms to settle before resuming watcher
            if watcher_was_active:
                QTimer.singleShot(500, self._resume_watcher_after_heal)
            self._on_auto_heal_completed(result, profile.name, notify_if_clean)

        self.auto_heal_thread.finished.connect(_on_finished)
        self.auto_heal_thread.start(QThread.LowPriority)

    def _resume_watcher_after_heal(self) -> None:
        if self.protection_active and not self.protection_paused and self.file_watcher:
            self.file_watcher.resume()

    def _on_auto_heal_completed(self, result: AutoHealResult, game_name: str, notify_if_clean: bool) -> None:
        if result.restored_files:
            count = len(result.restored_files)
            logger.info("Auto-heal verified and restored %d missing save files for '%s'.", count, game_name)
            self.notify(
                "Game Save Guardian",
                f"[{game_name}] Auto-Heal: Restored {count} missing save file(s) from backup!",
                is_critical=False,
                is_manual=notify_if_clean
            )
            self._refresh_metrics()
            self.page_history.refresh_records()
            if notify_if_clean:
                sample_files = "\n".join(f"• {f}" for f in result.restored_files[:8])
                if count > 8:
                    sample_files += f"\n...and {count - 8} more"
                QMessageBox.information(
                    self,
                    "Missing Saves Restored",
                    f"Successfully restored {count} missing save file(s) for '{game_name}' from backup:\n\n{sample_files}"
                )
        else:
            if notify_if_clean:
                QMessageBox.information(
                    self,
                    "Save Verification",
                    f"[{game_name}] All save files are verified and present in the original folder.\nNo missing files found in backup."
                )

    def _open_backup_folder(self) -> None:
        dest = self.settings.get_backup_dir_for_game(self.settings.active_game)
        dest.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(dest))
        else:
            subprocess.run(["explorer", str(dest)])
            QMessageBox.warning(self, "Folder Missing", "Backup folder has not been created or does not exist.")

    def _open_first_run_wizard(self) -> None:
        wizard = SetupWizardDialog(self.db, self.settings, self)
        wizard.setup_finished.connect(self._on_wizard_finished)
        wizard.exec()

    def _on_wizard_finished(self, updated_settings: AppSettings) -> None:
        self.settings = updated_settings
        self.config_mgr.save_config(self.settings)
        self.backup_mgr = BackupManager(self.db, self.settings)
        self._start_protection()
        self._refresh_metrics()

    def _on_settings_updated(self, updated_settings: AppSettings) -> None:
        self.settings = updated_settings
        self.backup_mgr = BackupManager(self.db, self.settings)
        self._start_protection()
        self._refresh_metrics()
        if hasattr(self, "page_history"):
            self.page_history.settings = self.settings
            self.page_history.set_active_game_filter(self.settings.active_game.name, sync_global=False)

    def _on_theme_changed(self, theme_name: str) -> None:
        self._apply_theme()

    def _on_language_changed(self, lang_code: str) -> None:
        self._apply_language()

    def _apply_theme(self) -> None:
        self.setStyleSheet(get_stylesheet(self.settings.theme))

    def _apply_language(self) -> None:
        set_language(self.settings.language)
        if is_rtl():
            QApplication.setLayoutDirection(Qt.RightToLeft)
        else:
            QApplication.setLayoutDirection(Qt.LeftToRight)

    def _show_window(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _on_game_exited(self, game_name: Optional[str] = None) -> None:
        """Triggered when monitored game process transitions from running -> exited."""
        target_name = game_name if (game_name and game_name != "default") else self.settings.active_game.name
        logger.info("Monitored game '%s' exited. Scheduling post-session safety backup and maintenance.", target_name)
        target_profile = next((g for g in self.settings.games if g.name == target_name), self.settings.active_game)
        self.trigger_backup(trigger=BackupTrigger.REALTIME, force=True, game_profile=target_profile)
        if self.settings.get_archive_mode_for_game(target_profile) == ArchiveMode.SINGLE_VAULT.value:
            dest = self.settings.get_backup_dir_for_game(target_profile)
            vault_name = get_master_archive_filename(target_profile.name)
            vault_path = dest / vault_name
            if vault_path.is_file():
                check_and_compact_vault_if_needed(vault_path, threshold_ratio=0.25)

        if self.settings.auto_heal_missing_saves:
            QTimer.singleShot(3500, lambda: self.check_and_auto_heal_saves(target_profile, notify_if_clean=False))

    def _on_switch_active_game(self, game_name: str) -> None:
        """Switch active game profile globally across Dashboard, History, and Settings."""
        if not game_name or game_name == "all_games":
            return

        if self.settings.set_active_game(game_name):
            logger.info("Switched active game profile globally to: %s", game_name)
            # Self-healing if save directory missing
            if not self.settings.active_game.save_directory and "witcher" in game_name.lower():
                from src.utils.paths import detect_witcher_3_save_paths
                w3 = detect_witcher_3_save_paths()
                if w3:
                    self.settings.active_game.save_directory = str(w3[0])

            self.config_mgr.save_config(self.settings)
            self.backup_mgr = BackupManager(self.db, self.settings)
            self._start_protection()
            self._refresh_metrics()
            self.page_history.set_active_game_filter(game_name, sync_global=False)
            if hasattr(self, "page_settings"):
                self.page_settings.set_active_game_profile(game_name, emit_switch=False)
            if self.settings.auto_heal_missing_saves:
                QTimer.singleShot(1000, lambda: self.check_and_auto_heal_saves(notify_if_clean=False))

    def _on_dashboard_compact_vault(self) -> None:
        """Manually compact master vault from dashboard."""
        dest = self.settings.get_backup_dir_for_game(self.settings.active_game)
        vault_name = get_master_archive_filename(self.settings.active_game.name)
        vault_path = dest / vault_name
        if not vault_path.is_file():
            QMessageBox.information(self, tr("btn_compact_vault"), "No master vault archive exists yet to compact.")
            return

        ok, old_s, new_s = compact_vault(vault_path)
        if ok:
            reclaimed = max(0, old_s - new_s)
            QMessageBox.information(
                self, tr("btn_compact_vault"),
                f"Vault compacted successfully!\nOld Size: {format_bytes(old_s)}\nNew Size: {format_bytes(new_s)}\nReclaimed Space: {format_bytes(reclaimed)}"
            )
            self._refresh_metrics()
            self.page_history.refresh_records()
        else:
            QMessageBox.warning(
                self, tr("btn_compact_vault"),
                "Vault compaction could not be completed or archive is already optimal."
            )

    def hideEvent(self, event) -> None:
        """Pause UI refresh timers and trim process working set memory when hidden."""
        if hasattr(self, "countdown_timer") and self.countdown_timer.isActive():
            self.countdown_timer.stop()
        if hasattr(self, "page_dashboard"):
            self.page_dashboard.pause_timers()

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
            except Exception:
                pass

        super().hideEvent(event)

    def showEvent(self, event) -> None:
        """Resume UI refresh timers and immediately refresh metrics when shown."""
        if hasattr(self, "countdown_timer") and not self.countdown_timer.isActive():
            self.countdown_timer.start(1000)
        if hasattr(self, "page_dashboard"):
            self.page_dashboard.resume_timers()

        self._refresh_metrics()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self.settings.minimize_to_tray:
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "Game Save Guardian",
                "Protection is still running in the background.",
                QSystemTrayIcon.Information,
                2000
            )
        else:
            self._exit_app()

    def _exit_app(self) -> None:
        if self.file_watcher:
            self.file_watcher.stop()
        self.sched_timer.stop()
        self.countdown_timer.stop()
        QApplication.quit()
