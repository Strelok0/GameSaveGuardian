"""
Main Dashboard page displaying live protection metrics, system health, and control actions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.models import AppSettings, BackupRecord, BackupStatus, ProtectionMode
from src.utils.i18n import tr
from src.utils.paths import format_bytes, get_disk_free_space


class DashboardPage(QWidget):
    """Primary application overview screen."""

    request_start_protection = Signal()
    request_pause_protection = Signal()
    request_resume_protection = Signal()
    request_stop_protection = Signal()
    request_backup_now = Signal()
    request_open_restore = Signal()
    request_open_backup_folder = Signal()
    request_open_settings = Signal()
    request_switch_game = Signal(str)
    request_compact_vault = Signal()
    request_refresh_metrics = Signal()

    def __init__(self, settings: AppSettings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.settings = settings
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 28, 28, 28)
        main_layout.setSpacing(20)

        # Header Row: Title, Game Profile Switcher & Status Badge
        header_layout = QHBoxLayout()
        title_box = QVBoxLayout()

        top_title_row = QHBoxLayout()
        top_title_row.setSpacing(12)

        self.game_title_lbl = QLabel(f"🎮 {self.settings.active_game.name}")
        self.game_title_lbl.setStyleSheet("font-size: 22px; font-weight: 700; color: #ffffff;")
        top_title_row.addWidget(self.game_title_lbl)

        self.combo_games = QComboBox()
        self.combo_games.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; border: 1px solid #334155; border-radius: 6px; padding: 4px 12px; font-weight: 600; font-size: 13px;"
        )
        self._refresh_games_combo()
        self.combo_games.currentIndexChanged.connect(self._on_game_index_changed)
        top_title_row.addWidget(self.combo_games)
        top_title_row.addStretch()
        title_box.addLayout(top_title_row)

        path_row = QHBoxLayout()
        path_row.setSpacing(10)
        self.game_path_lbl = QLabel(self._format_save_path_display())
        self.game_path_lbl.setStyleSheet("font-size: 13px; color: #94a3b8;")
        path_row.addWidget(self.game_path_lbl)

        self.btn_quick_config = QPushButton("⚙️ " + tr("btn_browse"))
        self.btn_quick_config.setProperty("class", "secondary-btn")
        self.btn_quick_config.setFixedHeight(26)
        self.btn_quick_config.clicked.connect(self.request_open_settings.emit)
        path_row.addWidget(self.btn_quick_config)
        path_row.addStretch()

        title_box.addLayout(path_row)
        header_layout.addLayout(title_box)

        header_layout.addStretch()

        self.status_badge = QLabel(tr("status_active"))
        self.status_badge.setProperty("class", "badge-active")
        self.status_badge.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self.status_badge)
        main_layout.addLayout(header_layout)

        # Action Buttons Bar
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.btn_backup_now = QPushButton(tr("btn_backup_now"))
        self.btn_backup_now.setProperty("class", "primary-btn")
        self.btn_backup_now.clicked.connect(self.request_backup_now.emit)
        action_bar.addWidget(self.btn_backup_now)

        self.btn_toggle_protection = QPushButton(tr("btn_pause_protection"))
        self.btn_toggle_protection.setProperty("class", "secondary-btn")
        self.btn_toggle_protection.clicked.connect(self._on_toggle_protection_clicked)
        action_bar.addWidget(self.btn_toggle_protection)

        self.btn_compact_vault = QPushButton(tr("btn_compact_vault"))
        self.btn_compact_vault.setProperty("class", "secondary-btn")
        self.btn_compact_vault.clicked.connect(self.request_compact_vault.emit)
        action_bar.addWidget(self.btn_compact_vault)

        self.btn_restore = QPushButton(tr("btn_restore"))
        self.btn_restore.setProperty("class", "secondary-btn")
        self.btn_restore.clicked.connect(self.request_open_restore.emit)
        action_bar.addWidget(self.btn_restore)

        self.btn_open_folder = QPushButton(tr("btn_open_folder"))
        self.btn_open_folder.setProperty("class", "secondary-btn")
        self.btn_open_folder.clicked.connect(self.request_open_backup_folder.emit)
        action_bar.addWidget(self.btn_open_folder)

        action_bar.addStretch()
        main_layout.addLayout(action_bar)

        # Dashboard polling timer for UI refresh
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.timeout.connect(self.request_refresh_metrics.emit)
        self.dashboard_timer.start(5000)

        # 6-Card Metric Grid
        grid = QGridLayout()
        grid.setSpacing(16)

        # Card 1: Last Backup Time
        self.card_last_backup, self.val_last_backup = self._create_card(tr("card_last_backup"), "Never")
        grid.addWidget(self.card_last_backup, 0, 0)

        # Card 2: Next Safety Scan
        self.card_next_scan, self.val_next_scan = self._create_card(tr("card_next_scan"), "--")
        grid.addWidget(self.card_next_scan, 0, 1)

        # Card 3: Backup Mode
        self.card_mode, self.val_mode = self._create_card(tr("card_mode"), self._get_mode_display())
        grid.addWidget(self.card_mode, 0, 2)

        # Card 4: Total Backups Count
        self.card_count, self.val_count = self._create_card(tr("card_backup_count"), "0")
        grid.addWidget(self.card_count, 1, 0)

        # Card 5: Total Backup Storage Used
        self.card_storage, self.val_storage = self._create_card(tr("card_storage_used"), "0 MB")
        grid.addWidget(self.card_storage, 1, 1)

        # Card 6: Available Destination Disk Space
        self.card_free_space, self.val_free_space = self._create_card(tr("card_free_space"), "Checking...")
        grid.addWidget(self.card_free_space, 1, 2)

        main_layout.addLayout(grid)

        # Error / Alert Banner
        self.alert_banner = QFrame()
        self.alert_banner.setStyleSheet("background-color: #451a03; border: 1px solid #d97706; border-radius: 8px; padding: 12px;")
        self.alert_banner.setVisible(False)
        alert_layout = QHBoxLayout(self.alert_banner)
        alert_layout.setContentsMargins(12, 8, 12, 8)
        self.alert_text_lbl = QLabel("")
        self.alert_text_lbl.setStyleSheet("color: #fef3c7; font-weight: 500;")
        alert_layout.addWidget(self.alert_text_lbl)
        main_layout.addWidget(self.alert_banner)

        main_layout.addStretch()

    def _create_card(self, title: str, initial_value: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setProperty("class", "metric-card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        lbl_title = QLabel(title)
        lbl_title.setProperty("class", "card-title")
        lbl_value = QLabel(initial_value)
        lbl_value.setProperty("class", "card-value")

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_value)
        return card, lbl_value

    def pause_timers(self) -> None:
        """Pause UI refresh timer when window is hidden."""
        if hasattr(self, "dashboard_timer") and self.dashboard_timer.isActive():
            self.dashboard_timer.stop()

    def resume_timers(self) -> None:
        """Resume UI refresh timer when window is shown."""
        if hasattr(self, "dashboard_timer") and not self.dashboard_timer.isActive():
            self.dashboard_timer.start(5000)

    def _format_save_path_display(self) -> str:
        sd = self.settings.active_game.save_directory
        if sd:
            return f"📁 {sd}"
        return f"⚠️ {tr('status_unconfigured')}"

    def _refresh_games_combo(self) -> None:
        if not hasattr(self, "combo_games"):
            return
        self.combo_games.blockSignals(True)
        self.combo_games.clear()
        selected_idx = 0
        for idx, g in enumerate(self.settings.games):
            self.combo_games.addItem(f"🎮 {g.name}", g.name)
            if g.name == self.settings.active_game.name:
                selected_idx = idx
        if self.settings.games:
            self.combo_games.setCurrentIndex(selected_idx)
        self.combo_games.blockSignals(False)

    def _on_game_index_changed(self, index: int) -> None:
        if index < 0:
            return
        game_name = self.combo_games.itemData(index)
        if game_name and game_name != self.settings.active_game.name:
            self.request_switch_game.emit(game_name)

    def _on_game_selected(self, game_name: str) -> None:
        if game_name and game_name != self.settings.active_game.name:
            self.request_switch_game.emit(game_name)

    def _get_mode_display(self) -> str:
        mode = self.settings.backup_mode
        if mode == ProtectionMode.HYBRID.value:
            return "Hybrid"
        elif mode == ProtectionMode.REALTIME.value:
            return "Real-Time"
        elif mode == ProtectionMode.SCHEDULED.value:
            return f"Scheduled ({self.settings.scheduled_interval_minutes}m)"
        return mode

    def _on_toggle_protection_clicked(self) -> None:
        btn_text = self.btn_toggle_protection.text()
        if btn_text == tr("btn_pause_protection"):
            self.request_pause_protection.emit()
        elif btn_text == tr("btn_resume_protection"):
            self.request_resume_protection.emit()
        elif btn_text == tr("btn_start_protection"):
            self.request_start_protection.emit()

    def update_metrics(
        self,
        protection_active: bool,
        protection_paused: bool,
        total_backups: int,
        total_bytes: int,
        last_backup_rec: Optional[BackupRecord] = None,
        next_scan_text: str = "--",
        warning_msg: Optional[str] = None
    ) -> None:
        """Refresh dashboard numbers and states."""
        # 1. Update Game info
        self.game_title_lbl.setText(f"🎮 {self.settings.active_game.name}")
        self.game_path_lbl.setText(self._format_save_path_display())
        if not self.settings.active_game.save_directory:
            self.game_path_lbl.setStyleSheet("font-size: 13px; color: #f59e0b; font-weight: 600;")
            if hasattr(self, "btn_quick_config"):
                self.btn_quick_config.setVisible(True)
        else:
            self.game_path_lbl.setStyleSheet("font-size: 13px; color: #94a3b8;")
            if hasattr(self, "btn_quick_config"):
                self.btn_quick_config.setVisible(False)

        self._refresh_games_combo()

        # 2. Status Badge & Protection Button
        if not self.settings.active_game.save_directory:
            self.status_badge.setText(tr("status_unconfigured"))
            self.status_badge.setProperty("class", "badge-error")
            self.btn_toggle_protection.setText(tr("btn_start_protection"))
            self.btn_toggle_protection.setEnabled(False)
        elif protection_paused:
            self.status_badge.setText(tr("status_paused"))
            self.status_badge.setProperty("class", "badge-paused")
            self.btn_toggle_protection.setText(tr("btn_resume_protection"))
            self.btn_toggle_protection.setEnabled(True)
        elif protection_active:
            self.status_badge.setText(tr("status_active"))
            self.status_badge.setProperty("class", "badge-active")
            self.btn_toggle_protection.setText(tr("btn_pause_protection"))
            self.btn_toggle_protection.setEnabled(True)
        else:
            self.status_badge.setText(tr("status_stopped"))
            self.status_badge.setProperty("class", "badge-paused")
            self.btn_toggle_protection.setText(tr("btn_start_protection"))
            self.btn_toggle_protection.setEnabled(True)

        # Style re-polish
        self.status_badge.style().unpolish(self.status_badge)
        self.status_badge.style().polish(self.status_badge)

        # 3. Cards
        self.val_count.setText(str(total_backups))
        self.val_storage.setText(format_bytes(total_bytes))
        self.val_mode.setText(self._get_mode_display())
        self.val_next_scan.setText(next_scan_text)

        if last_backup_rec:
            # Format timestamp nicely
            ts = last_backup_rec.timestamp.replace("T", " ")[:19]
            self.val_last_backup.setText(ts)
        else:
            self.val_last_backup.setText("None yet")

        # Free space
        try:
            dest = self.settings.get_backup_dir_for_game(self.settings.active_game)
            free = get_disk_free_space(dest)
            self.val_free_space.setText(format_bytes(free))
        except Exception:
            self.val_free_space.setText("--")

        # Warning banner
        if warning_msg:
            self.alert_text_lbl.setText(warning_msg)
            self.alert_banner.setVisible(True)
        else:
            self.alert_banner.setVisible(False)
