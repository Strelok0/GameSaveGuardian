"""
Settings page for Game Save Guardian.
Allows configuration of directories, trigger modes, retention policies, theme, language, and logs.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.backup.zip_writer import compact_vault, get_master_archive_filename
from src.config import ConfigManager
from src.models import AppSettings, GameProfile, ProtectionMode
from src.utils.i18n import get_language, set_language, tr
from src.utils.logging_config import export_diagnostic_logs
from src.utils.paths import (
    auto_detect_all_games,
    detect_witcher_3_save_paths,
    format_bytes,
    get_default_backup_dir,
    get_logs_dir,
    is_path_overlapping_or_recursive,
    is_same_physical_disk,
)


class SettingsPage(QWidget):
    """Full settings configuration screen."""

    settings_updated = Signal(AppSettings)
    theme_changed = Signal(str)
    language_changed = Signal(str)
    request_switch_game = Signal(str)

    def __init__(self, config_manager: ConfigManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config_mgr = config_manager
        self.settings = self.config_mgr.get_settings()
        self._init_ui()

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(20, 16, 20, 16)
        outer_layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        outer_layout.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 10, 4)
        layout.setSpacing(18)

        # 1. Game & Directories
        grp_game = QGroupBox(tr("settings_general").replace("&", "&&"))
        f_game = QFormLayout(grp_game)
        f_game.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        # Profile Switcher / Selector + Delete Profile Button
        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)

        self.combo_profile_selector = QComboBox()
        self.combo_profile_selector.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; border: 1px solid #334155; border-radius: 6px; padding: 4px 10px; font-weight: 600;"
        )
        self._refresh_profile_selector()
        self.combo_profile_selector.currentIndexChanged.connect(self._on_profile_selector_changed)
        profile_row.addWidget(self.combo_profile_selector, stretch=1)

        self.btn_delete_profile = QPushButton("🗑️ " + tr("btn_delete_profile"))
        self.btn_delete_profile.setProperty("class", "secondary-btn")
        self.btn_delete_profile.setStyleSheet(
            "QPushButton { background-color: #7f1d1d; color: #fca5a5; border: 1px solid #991b1b; border-radius: 6px; padding: 4px 12px; font-weight: 600; }"
            "QPushButton:hover { background-color: #991b1b; color: #ffffff; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; border: 1px solid #475569; }"
        )
        self.btn_delete_profile.clicked.connect(self._on_delete_profile_clicked)
        self.btn_delete_profile.setEnabled(len(self.settings.games) > 1)
        profile_row.addWidget(self.btn_delete_profile)

        f_game.addRow("🎮 " + tr("field_game_name") + ":", profile_row)

        self.edit_game_name = QLineEdit(self.settings.active_game.name)
        f_game.addRow(tr("field_game_name") + " (Display):", self.edit_game_name)

        # Source dir: Primary LineEdit + Browse button
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        self.edit_save_dir = QLineEdit(self.settings.active_game.save_directory)
        btn_browse_src = QPushButton("📁 " + tr("btn_browse"))
        btn_browse_src.setProperty("class", "secondary-btn")
        btn_browse_src.clicked.connect(self._browse_save_dir)
        src_row.addWidget(self.edit_save_dir, 1)
        src_row.addWidget(btn_browse_src)
        f_game.addRow(tr("field_save_dir") + ":", src_row)

        # Source dir detection action buttons row (dedicated row prevents cramping)
        detect_row = QHBoxLayout()
        detect_row.setSpacing(8)
        btn_auto_src = QPushButton("🔍 " + tr("btn_auto_detect"))
        btn_auto_src.setProperty("class", "secondary-btn")
        btn_auto_src.clicked.connect(self._auto_detect_src)

        btn_detect_all = QPushButton("🎮 " + tr("btn_detect_all_games"))
        btn_detect_all.setProperty("class", "secondary-btn")
        btn_detect_all.clicked.connect(self._auto_detect_all_games)

        detect_row.addWidget(btn_auto_src)
        detect_row.addWidget(btn_detect_all)
        detect_row.addStretch()
        f_game.addRow("", detect_row)

        # Backup dir
        dest_row = QHBoxLayout()
        dest_row.setSpacing(8)
        self.edit_backup_dir = QLineEdit(
            self.settings.unified_backup_directory if self.settings.use_unified_backup_dir else self.settings.active_game.backup_directory
        )
        self.edit_backup_dir.textChanged.connect(lambda: self._update_unified_hint())
        btn_browse_dest = QPushButton("📁 " + tr("btn_browse"))
        btn_browse_dest.setProperty("class", "secondary-btn")
        btn_browse_dest.clicked.connect(self._browse_backup_dir)
        dest_row.addWidget(self.edit_backup_dir, 1)
        dest_row.addWidget(btn_browse_dest)

        self.lbl_backup_dir_title = QLabel(
            (tr("field_unified_backup_dir") if self.settings.use_unified_backup_dir else tr("field_backup_dir")) + ":"
        )
        f_game.addRow(self.lbl_backup_dir_title, dest_row)

        # Unified Backup Root Checkbox & Hint (requested right below Backup Destination Directory)
        self.chk_use_unified_backup = QCheckBox(tr("chk_use_unified_backup"))
        self.chk_use_unified_backup.setChecked(self.settings.use_unified_backup_dir)
        self.chk_use_unified_backup.toggled.connect(self._on_unified_backup_toggled)
        f_game.addRow("", self.chk_use_unified_backup)

        self.lbl_unified_backup_hint = QLabel("")
        self.lbl_unified_backup_hint.setStyleSheet("color: #38bdf8; font-size: 11px; padding: 2px 0 6px 0;")
        self.lbl_unified_backup_hint.setWordWrap(True)
        f_game.addRow("", self.lbl_unified_backup_hint)

        self.disk_warn_lbl = QLabel("")
        self.disk_warn_lbl.setStyleSheet("color: #f59e0b; font-size: 11px;")
        f_game.addRow("", self.disk_warn_lbl)
        layout.addWidget(grp_game)

        # 2. Monitoring & Trigger Strategy
        grp_mon = QGroupBox(tr("settings_monitoring").replace("&", "&&"))
        f_mon = QFormLayout(grp_mon)
        f_mon.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem(tr("mode_hybrid"), ProtectionMode.HYBRID.value)
        self.combo_mode.addItem(tr("mode_realtime"), ProtectionMode.REALTIME.value)
        self.combo_mode.addItem(tr("mode_scheduled"), ProtectionMode.SCHEDULED.value)
        idx = self.combo_mode.findData(self.settings.backup_mode)
        if idx >= 0:
            self.combo_mode.setCurrentIndex(idx)
        f_mon.addRow(tr("field_trigger_mode") + ":", self.combo_mode)

        self.combo_archive_mode = QComboBox()
        self.combo_archive_mode.addItem(tr("archive_mode_single_vault"), "single_vault")
        self.combo_archive_mode.addItem(tr("archive_mode_snapshots"), "snapshots")
        a_idx = self.combo_archive_mode.findData(self.settings.archive_mode)
        if a_idx >= 0:
            self.combo_archive_mode.setCurrentIndex(a_idx)
        f_mon.addRow(tr("field_archive_mode") + ":", self.combo_archive_mode)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(1, 1440)
        self.spin_interval.setValue(self.settings.scheduled_interval_minutes)
        f_mon.addRow(tr("field_sched_interval") + ":", self.spin_interval)

        self.spin_debounce = QSpinBox()
        self.spin_debounce.setRange(1, 30)
        self.spin_debounce.setValue(int(self.settings.debounce_seconds))
        f_mon.addRow(tr("field_debounce") + ":", self.spin_debounce)

        self.spin_stability = QSpinBox()
        self.spin_stability.setRange(1, 10)
        self.spin_stability.setValue(self.settings.stability_check_count)
        f_mon.addRow(tr("field_stability_checks") + ":", self.spin_stability)

        self.combo_compression = QComboBox()
        self.combo_compression.addItem("Standard (Deflated)", "deflated")
        self.combo_compression.addItem("Fast / No Compression (Stored)", "stored")
        self.combo_compression.addItem("Maximum (Bzip2)", "bzip2")
        c_idx = self.combo_compression.findData(self.settings.compression_level)
        if c_idx >= 0:
            self.combo_compression.setCurrentIndex(c_idx)
        f_mon.addRow(tr("field_compression") + ":", self.combo_compression)

        layout.addWidget(grp_mon)

        # 3. Retention Policies
        grp_ret = QGroupBox(tr("settings_retention").replace("&", "&&"))
        f_ret = QFormLayout(grp_ret)
        f_ret.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.retention_note_lbl = QLabel("")
        self.retention_note_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; padding-bottom: 6px;")
        self.retention_note_lbl.setWordWrap(True)
        f_ret.addRow("", self.retention_note_lbl)

        self.chk_prune = QCheckBox(tr("field_enable_pruning"))
        self.chk_prune.setChecked(self.settings.retention.enabled)
        f_ret.addRow("", self.chk_prune)

        self.spin_max_count = QSpinBox()
        self.spin_max_count.setRange(1, 1000)
        self.spin_max_count.setValue(self.settings.retention.max_count)
        f_ret.addRow(tr("field_retention_max_count") + ":", self.spin_max_count)

        self.spin_keep_days = QSpinBox()
        self.spin_keep_days.setRange(1, 365)
        self.spin_keep_days.setValue(self.settings.retention.keep_all_days)
        f_ret.addRow(tr("field_retention_days") + ":", self.spin_keep_days)

        self.spin_daily_days = QSpinBox()
        self.spin_daily_days.setRange(1, 365)
        self.spin_daily_days.setValue(self.settings.retention.daily_snapshot_days)
        f_ret.addRow(tr("field_daily_retention_days") + ":", self.spin_daily_days)

        self.btn_compact_vault_settings = QPushButton(tr("btn_compact_vault"))
        self.btn_compact_vault_settings.setProperty("class", "secondary-btn")
        self.btn_compact_vault_settings.clicked.connect(self._compact_vault)
        f_ret.addRow("", self.btn_compact_vault_settings)

        layout.addWidget(grp_ret)

        # 4. System & Appearance
        grp_sys = QGroupBox(tr("settings_system").replace("&", "&&"))
        f_sys = QFormLayout(grp_sys)
        f_sys.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.chk_windows_startup = QCheckBox(tr("field_start_with_windows"))
        self.chk_windows_startup.setChecked(self.settings.start_with_windows)
        f_sys.addRow("", self.chk_windows_startup)

        self.chk_minimize_tray = QCheckBox(tr("field_minimize_to_tray"))
        self.chk_minimize_tray.setChecked(self.settings.minimize_to_tray)
        f_sys.addRow("", self.chk_minimize_tray)

        self.chk_silent_gaming = QCheckBox(tr("field_silent_gaming_mode"))
        self.chk_silent_gaming.setChecked(self.settings.silent_gaming_mode)
        f_sys.addRow("", self.chk_silent_gaming)

        # Diagnostic buttons
        diag_row = QHBoxLayout()
        diag_row.setSpacing(8)
        btn_open_logs = QPushButton(tr("btn_open_logs"))
        btn_open_logs.setProperty("class", "secondary-btn")
        btn_open_logs.clicked.connect(self._open_log_folder)
        btn_export_logs = QPushButton(tr("btn_export_logs"))
        btn_export_logs.setProperty("class", "secondary-btn")
        btn_export_logs.clicked.connect(self._export_logs)
        diag_row.addWidget(btn_open_logs)
        diag_row.addWidget(btn_export_logs)
        diag_row.addStretch()
        f_sys.addRow("Diagnostics:", diag_row)

        layout.addWidget(grp_sys)

        # Bottom Save Button Bar (permanently anchored outside scroll view)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 8, 0, 0)
        btn_bar.addStretch()
        self.btn_save = QPushButton(tr("btn_save_settings"))
        self.btn_save.setProperty("class", "primary-btn")
        self.btn_save.setMinimumWidth(160)
        self.btn_save.setFixedHeight(38)
        self.btn_save.clicked.connect(self._save_changes)
        btn_bar.addWidget(self.btn_save)
        outer_layout.addLayout(btn_bar)

        self.combo_archive_mode.currentIndexChanged.connect(lambda: self._update_retention_visibility())
        self.chk_prune.toggled.connect(lambda: self._update_retention_visibility())

        self._check_disk_warning()
        self._update_retention_visibility()
        self._update_unified_hint()

    def _update_retention_visibility(self) -> None:
        is_single = (self.combo_archive_mode.currentData() == "single_vault")
        if is_single:
            self.retention_note_lbl.setText(tr("retention_hint_single_vault"))
            self.chk_prune.setEnabled(False)
            self.spin_max_count.setEnabled(False)
            self.spin_keep_days.setEnabled(False)
            self.spin_daily_days.setEnabled(False)
        else:
            self.retention_note_lbl.setText("")
            self.chk_prune.setEnabled(True)
            enabled = self.chk_prune.isChecked()
            self.spin_max_count.setEnabled(enabled)
            self.spin_keep_days.setEnabled(enabled)
            self.spin_daily_days.setEnabled(enabled)

    def _browse_save_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Save Directory", self.edit_save_dir.text())
        if folder:
            self.edit_save_dir.setText(folder)
            self._check_disk_warning()

    def _auto_detect_src(self) -> None:
        paths = detect_witcher_3_save_paths()
        if paths:
            self.edit_save_dir.setText(str(paths[0]))
            self._check_disk_warning()
        else:
            QMessageBox.information(self, "Not Found", tr("wizard_witcher_not_found"))

    def _browse_backup_dir(self) -> None:
        title = "Select Unified Root Backup Directory" if self.chk_use_unified_backup.isChecked() else "Select Backup Destination"
        folder = QFileDialog.getExistingDirectory(self, title, self.edit_backup_dir.text())
        if folder:
            self.edit_backup_dir.setText(folder)
            if self.chk_use_unified_backup.isChecked():
                self.settings.unified_backup_directory = folder
            self._update_unified_hint()
            self._check_disk_warning()

    def _on_unified_backup_toggled(self, checked: bool) -> None:
        self.settings.use_unified_backup_dir = checked
        if checked:
            self.lbl_backup_dir_title.setText(tr("field_unified_backup_dir") + ":")
            self.edit_backup_dir.setText(self.settings.unified_backup_directory)
        else:
            self.lbl_backup_dir_title.setText(tr("field_backup_dir") + ":")
            self.edit_backup_dir.setText(self.settings.active_game.backup_directory)
        self._update_unified_hint()
        self._check_disk_warning()

    def _update_unified_hint(self) -> None:
        if not hasattr(self, "chk_use_unified_backup") or not hasattr(self, "lbl_unified_backup_hint"):
            return
        if self.chk_use_unified_backup.isChecked():
            root_dir = self.edit_backup_dir.text().strip() or self.settings.unified_backup_directory
            game_name = self.edit_game_name.text().strip() or self.settings.active_game.name
            safe_name = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in game_name).strip()
            subfolder = Path(root_dir) / safe_name
            self.lbl_unified_backup_hint.setText(
                f"💡 {tr('unified_backup_hint')}\n"
                f"📁 {game_name} ➔ {subfolder}"
            )
            self.lbl_unified_backup_hint.setVisible(True)
        else:
            self.lbl_unified_backup_hint.setText("")
            self.lbl_unified_backup_hint.setVisible(False)

    def _check_disk_warning(self) -> None:
        src = self.edit_save_dir.text().strip()
        dest = self.edit_backup_dir.text().strip()
        if src and dest and is_same_physical_disk(src, dest):
            self.disk_warn_lbl.setText(tr("msg_dest_same_disk"))
        else:
            self.disk_warn_lbl.setText("")

    def _open_log_folder(self) -> None:
        logs_path = get_logs_dir()
        if sys.platform == "win32":
            os.startfile(str(logs_path))
        else:
            subprocess.run(["explorer", str(logs_path)])

    def _export_logs(self) -> None:
        dest_zip, _ = QFileDialog.getSaveFileName(self, "Export Diagnostic Logs", "GameSaveGuardian_Logs.zip", "ZIP Archive (*.zip)")
        if dest_zip:
            try:
                export_diagnostic_logs(dest_zip)
                QMessageBox.information(self, "Export Succeeded", f"Diagnostic logs exported to:\n{dest_zip}")
            except Exception as e:
                QMessageBox.critical(self, "Export Failed", f"Could not export logs: {e}")

    def _refresh_profile_selector(self) -> None:
        if not hasattr(self, "combo_profile_selector"):
            return
        self.combo_profile_selector.blockSignals(True)
        self.combo_profile_selector.clear()
        selected_idx = 0
        for idx, g in enumerate(self.settings.games):
            self.combo_profile_selector.addItem(f"🎮 {g.name}", g.name)
            if g.name == self.settings.active_game.name:
                selected_idx = idx
        self.combo_profile_selector.addItem("➕ Add New Profile...", "__add_new__")
        if self.settings.games:
            self.combo_profile_selector.setCurrentIndex(selected_idx)
        self.combo_profile_selector.blockSignals(False)
        if hasattr(self, "btn_delete_profile"):
            self.btn_delete_profile.setEnabled(len(self.settings.games) > 1)

    def _on_profile_selector_changed(self, index: int) -> None:
        if index < 0 or not hasattr(self, "combo_profile_selector"):
            return
        data = self.combo_profile_selector.itemData(index)
        if data == "__add_new__":
            self.edit_game_name.setText("")
            self.edit_game_name.setPlaceholderText("Enter new game title...")
            self.edit_save_dir.setText("")
            self.edit_backup_dir.setText("")
            self.edit_game_name.setFocus()
            if hasattr(self, "btn_delete_profile"):
                self.btn_delete_profile.setEnabled(False)
        elif data:
            if hasattr(self, "btn_delete_profile"):
                self.btn_delete_profile.setEnabled(len(self.settings.games) > 1)
            self.set_active_game_profile(data, emit_switch=True)

    def _on_delete_profile_clicked(self) -> None:
        selected_data = self.combo_profile_selector.currentData() if hasattr(self, "combo_profile_selector") else None
        target_name = selected_data if (selected_data and selected_data != "__add_new__") else self.settings.active_game.name

        if len(self.settings.games) <= 1:
            QMessageBox.warning(self, tr("btn_delete_profile"), tr("msg_cannot_delete_last_profile"))
            return

        confirm = QMessageBox.question(
            self,
            tr("btn_delete_profile"),
            tr("msg_confirm_delete_profile").format(target_name),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        deleted = self.settings.delete_game_profile(target_name)
        if deleted:
            self.config_mgr.save_config(self.settings)
            self._refresh_profile_selector()
            self.set_active_game_profile(self.settings.active_game.name, emit_switch=True)
            self.settings_updated.emit(self.settings)
            self.request_switch_game.emit(self.settings.active_game.name)
            QMessageBox.information(
                self,
                tr("btn_delete_profile"),
                tr("msg_profile_deleted").format(target_name),
            )

    def set_active_game_profile(self, game_name: str, emit_switch: bool = False) -> None:
        """Switch form inputs to display fields for the specified game profile."""
        self._refresh_profile_selector()
        self.combo_profile_selector.blockSignals(True)
        idx = self.combo_profile_selector.findData(game_name)
        if idx >= 0:
            self.combo_profile_selector.setCurrentIndex(idx)
        self.combo_profile_selector.blockSignals(False)

        # Find profile in settings.games
        profile = next((g for g in self.settings.games if g.name == game_name), None)
        if profile is None and self.settings.active_game.name == game_name:
            profile = self.settings.active_game

        if profile:
            self.edit_game_name.setText(profile.name)
            self.edit_save_dir.setText(profile.save_directory)
            if self.chk_use_unified_backup.isChecked():
                self.edit_backup_dir.setText(self.settings.unified_backup_directory)
            else:
                self.edit_backup_dir.setText(profile.backup_directory)
            self._update_unified_hint()
            self._check_disk_warning()
            if hasattr(self, "btn_delete_profile"):
                self.btn_delete_profile.setEnabled(len(self.settings.games) > 1)
            if emit_switch and self.settings.active_game.name != game_name:
                self.request_switch_game.emit(game_name)

    def _save_changes(self) -> None:
        src = self.edit_save_dir.text().strip()
        dest = self.edit_backup_dir.text().strip()
        new_name = self.edit_game_name.text().strip()

        if src and dest:
            overlap, reason = is_path_overlapping_or_recursive(src, dest)
            if overlap:
                QMessageBox.critical(self, "Configuration Error", reason)
                return

        # Determine target game profile name
        selected_data = self.combo_profile_selector.currentData() if hasattr(self, "combo_profile_selector") else None
        target_name = new_name or (selected_data if selected_data != "__add_new__" else "") or self.settings.active_game.name

        if not target_name:
            QMessageBox.warning(self, "Game Title Missing", "Please specify a valid game title.")
            return

        is_unified = self.chk_use_unified_backup.isChecked()
        self.settings.use_unified_backup_dir = is_unified

        if is_unified:
            unified_dir = self.edit_backup_dir.text().strip() or self.settings.unified_backup_directory
            self.settings.unified_backup_directory = unified_dir

            # Apply dedicated subfolder to each game profile automatically
            for g in self.settings.games:
                safe_name = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in g.name).strip()
                g.backup_directory = str(Path(unified_dir) / safe_name)
            safe_active = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in self.settings.active_game.name).strip()
            self.settings.active_game.backup_directory = str(Path(unified_dir) / safe_active)

            safe_target = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in target_name).strip()
            dest = str(Path(unified_dir) / safe_target)
            self.settings.update_game_profile(
                name=target_name,
                save_directory=src,
                backup_directory=dest,
                new_name=new_name if new_name else None
            )
        else:
            if not dest:
                dest = str(get_default_backup_dir(target_name))
                self.edit_backup_dir.setText(dest)

            # Apply settings to both games list and active_game
            self.settings.update_game_profile(
                name=target_name,
                save_directory=src,
                backup_directory=dest,
                new_name=new_name if new_name else None
            )

        self.settings.set_active_game(new_name or target_name)

        self.settings.backup_mode = self.combo_mode.currentData()
        self.settings.archive_mode = self.combo_archive_mode.currentData()
        self.settings.scheduled_interval_minutes = self.spin_interval.value()
        self.settings.debounce_seconds = float(self.spin_debounce.value())
        self.settings.stability_check_count = self.spin_stability.value()
        self.settings.compression_level = self.combo_compression.currentData()

        self.settings.retention.enabled = self.chk_prune.isChecked()
        self.settings.retention.max_count = self.spin_max_count.value()
        self.settings.retention.keep_all_days = self.spin_keep_days.value()
        self.settings.retention.daily_snapshot_days = self.spin_daily_days.value()

        # Enforce dark theme and English language as requested
        self.settings.theme = "dark"
        self.settings.language = "en"

        self.settings.start_with_windows = self.chk_windows_startup.isChecked()
        self.settings.minimize_to_tray = self.chk_minimize_tray.isChecked()
        self.settings.silent_gaming_mode = self.chk_silent_gaming.isChecked()

        # Handle Windows Startup registry entry
        self._set_windows_startup(self.settings.start_with_windows)

        # Save to disk
        self.config_mgr.save_config(self.settings)
        self._refresh_profile_selector()

        self.settings_updated.emit(self.settings)
        QMessageBox.information(self, "Settings Saved", f"Profile '{self.settings.active_game.name}' and application settings saved successfully.")

    def _auto_detect_all_games(self) -> None:
        games = auto_detect_all_games()
        if not games:
            QMessageBox.information(self, "Auto-Detect Games", "No game save directories were automatically discovered.")
            return

        for name, save_path in games.items():
            profile = GameProfile(
                name=name,
                save_directory=str(save_path),
                backup_directory=str(get_default_backup_dir(name))
            )
            self.settings.add_game_profile(profile)

        # If current save dir was unconfigured, set to first detected
        first_name = list(games.keys())[0]
        first_path = games[first_name]
        if not self.edit_save_dir.text().strip():
            self.edit_game_name.setText(first_name)
            self.edit_save_dir.setText(str(first_path))
            self.edit_backup_dir.setText(str(get_default_backup_dir(first_name)))
            self.settings.set_active_game(first_name)

        self._refresh_profile_selector()
        self.config_mgr.save_config(self.settings)
        self.settings_updated.emit(self.settings)

        summary_list = "\n".join(f"• {k}: {v}" for k, v in list(games.items())[:8])
        QMessageBox.information(
            self, "Auto-Detect Games",
            f"Successfully scanned and identified {len(games)} game profile(s):\n\n{summary_list}"
        )

    def _compact_vault(self) -> None:
        dest = Path(self.edit_backup_dir.text().strip())
        game_name = self.edit_game_name.text().strip()
        vault_name = get_master_archive_filename(game_name)
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
        else:
            QMessageBox.warning(
                self, tr("btn_compact_vault"),
                "Vault compaction could not be completed or archive is already optimal."
            )

    def _set_windows_startup(self, enable: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
            )
            app_name = "GameSaveGuardian"
            if enable:
                exe_path = sys.executable
                if getattr(sys, "frozen", False):
                    exe_path = sys.executable
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, f'"{exe_path}" --minimized')
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
