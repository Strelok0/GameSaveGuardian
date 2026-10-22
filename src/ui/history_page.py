"""
Backup History and archive management page.
Provides filtering, inspection of ZIP contents, re-verification, restore, and safe deletion.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.backup.integrity import verify_zip_archive
from src.backup.zip_writer import (
    calculate_vault_dead_space,
    compact_vault,
    extract_screenshot_from_zip,
)
from src.database import DatabaseManager
from src.models import AppSettings, BackupRecord, BackupStatus, VerificationStatus
from src.restore.restore_manager import RestoreManager
from src.utils.i18n import tr
from src.utils.paths import format_bytes


class ZipInspectorDialog(QDialog):
    """Modal displaying contents of a specific ZIP archive."""

    def __init__(self, zip_path: str, restore_mgr: RestoreManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Archive Inspector: {Path(zip_path).name}")
        self.resize(600, 420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        info_lbl = QLabel(f"<b>Path:</b> {zip_path}")
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Relative File Path", "Uncompressed Size", "Modified Inside ZIP"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(table)

        ok, items, err = restore_mgr.inspect_archive(zip_path)
        if ok:
            table.setRowCount(len(items))
            for row, item in enumerate(items):
                table.setItem(row, 0, QTableWidgetItem(item["filename"]))
                table.setItem(row, 1, QTableWidgetItem(format_bytes(item["size"])))
                table.setItem(row, 2, QTableWidgetItem(item["date_time"]))
        else:
            layout.addWidget(QLabel(f"<font color='#ef4444'>{err}</font>"))

        close_btn = QPushButton("Close")
        close_btn.setProperty("class", "secondary-btn")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignRight)


class HistoryPage(QWidget):
    """Backup records browser and management interface."""

    request_restore_backup = Signal(BackupRecord)
    request_manual_backup = Signal()
    request_switch_game = Signal(str)

    def __init__(
        self,
        db_manager: DatabaseManager,
        restore_manager: RestoreManager,
        settings: Optional[AppSettings] = None,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.db = db_manager
        self.restore_mgr = restore_manager
        self.settings = settings
        self._records: List[BackupRecord] = []
        self._init_ui()
        self._refresh_game_filter_options()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)

        # Top Control Bar: Filters & Actions
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        # Game Profile Filter
        self.game_filter = QComboBox()
        self.game_filter.setStyleSheet(
            "background-color: #1e293b; color: #f8fafc; border: 1px solid #334155; border-radius: 6px; padding: 5px 12px; font-weight: 600;"
        )
        self.game_filter.currentIndexChanged.connect(self._on_game_filter_changed)
        top_bar.addWidget(self.game_filter)

        # Status Filter
        self.status_filter = QComboBox()
        self.status_filter.addItem(tr("filter_all"), "all")
        self.status_filter.addItem(tr("filter_success"), BackupStatus.SUCCESS.value)
        self.status_filter.addItem(tr("filter_failed"), BackupStatus.FAILED.value)
        self.status_filter.currentIndexChanged.connect(self.refresh_records)
        top_bar.addWidget(self.status_filter)

        # Search Bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search archive filename or date...")
        self.search_edit.textChanged.connect(self._apply_search_filter)
        top_bar.addWidget(self.search_edit, 1)

        # Refresh Button
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("class", "secondary-btn")
        refresh_btn.clicked.connect(self.refresh_records)
        top_bar.addWidget(refresh_btn)

        # Manual Backup Button
        backup_btn = QPushButton(tr("btn_backup_now"))
        backup_btn.setProperty("class", "primary-btn")
        backup_btn.clicked.connect(self.request_manual_backup.emit)
        top_bar.addWidget(backup_btn)

        main_layout.addLayout(top_bar)

        # Backups Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            tr("col_timestamp"),
            tr("col_filename"),
            tr("col_size"),
            tr("col_files"),
            tr("col_trigger"),
            tr("col_duration"),
            tr("col_verified"),
            tr("col_status"),
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(55)

        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)

        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_table_selection_changed)

        # Thumbnail & Details Inspector Panel
        self.preview_panel = QFrame()
        self.preview_panel.setProperty("class", "metric-card")
        self.preview_panel.setMinimumWidth(220)
        self.preview_panel.setMaximumWidth(320)
        p_layout = QVBoxLayout(self.preview_panel)
        p_layout.setContentsMargins(14, 14, 14, 14)
        p_layout.setSpacing(10)

        lbl_header = QLabel(f"<b>{tr('preview_thumbnail')}</b>")
        lbl_header.setStyleSheet("color: #38bdf8; font-size: 13px; font-weight: 600; background: transparent;")
        p_layout.addWidget(lbl_header)

        self.lbl_preview = QLabel()
        self.lbl_preview.setMinimumHeight(120)
        self.lbl_preview.setMaximumHeight(160)
        self.lbl_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.lbl_preview.setStyleSheet(
            "border: 1px solid #334155; border-radius: 8px; background-color: #0f172a; color: #94a3b8; font-size: 11px;"
        )
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.lbl_preview.setText(tr("no_screenshot"))
        self.preview_pixmap: Optional[QPixmap] = None
        p_layout.addWidget(self.lbl_preview)

        self.lbl_detail_filename = QLabel("")
        self.lbl_detail_filename.setStyleSheet("color: #f8fafc; font-weight: 600; font-size: 12px; background: transparent;")
        self.lbl_detail_filename.setWordWrap(True)
        p_layout.addWidget(self.lbl_detail_filename)

        self.lbl_detail_meta = QLabel("Select a backup to view details.")
        self.lbl_detail_meta.setStyleSheet("color: #94a3b8; font-size: 11px; background: transparent;")
        self.lbl_detail_meta.setWordWrap(True)
        p_layout.addWidget(self.lbl_detail_meta)

        # Kept for backward compatibility
        self.lbl_preview_info = QLabel("")
        self.lbl_preview_info.setVisible(False)
        p_layout.addWidget(self.lbl_preview_info)

        p_layout.addStretch()

        # Resizable Splitter between Table and Inspector Panel
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.preview_panel)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, True)

        main_layout.addWidget(self.splitter, 1)

        # Bottom Actions Bar
        bot_bar = QHBoxLayout()
        bot_bar.setSpacing(10)

        self.btn_inspect = QPushButton("Inspect Archive")
        self.btn_inspect.setProperty("class", "secondary-btn")
        self.btn_inspect.clicked.connect(self._on_inspect_clicked)
        bot_bar.addWidget(self.btn_inspect)

        self.btn_compact_vault = QPushButton(tr("btn_compact_vault"))
        self.btn_compact_vault.setProperty("class", "secondary-btn")
        self.btn_compact_vault.clicked.connect(self._on_compact_vault_clicked)
        bot_bar.addWidget(self.btn_compact_vault)

        self.btn_verify = QPushButton(tr("btn_verify"))
        self.btn_verify.setProperty("class", "secondary-btn")
        self.btn_verify.clicked.connect(self._on_reverify_clicked)
        bot_bar.addWidget(self.btn_verify)

        self.btn_restore = QPushButton(tr("btn_restore"))
        self.btn_restore.setProperty("class", "primary-btn")
        self.btn_restore.clicked.connect(self._on_restore_clicked)
        bot_bar.addWidget(self.btn_restore)

        self.btn_open_folder = QPushButton(tr("btn_open_folder"))
        self.btn_open_folder.setProperty("class", "secondary-btn")
        self.btn_open_folder.clicked.connect(self._on_open_folder_clicked)
        bot_bar.addWidget(self.btn_open_folder)

        bot_bar.addStretch()

        self.btn_delete = QPushButton(tr("btn_delete"))
        self.btn_delete.setProperty("class", "danger-btn")
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        bot_bar.addWidget(self.btn_delete)

        main_layout.addLayout(bot_bar)

    def _refresh_game_filter_options(self) -> None:
        """Populate the game dropdown with all available profiles and active DB games."""
        current_data = self.game_filter.currentData() if hasattr(self, "game_filter") else None
        self.game_filter.blockSignals(True)
        self.game_filter.clear()

        # Gather game titles from settings
        known_games = set()
        if self.settings:
            for p in self.settings.games:
                if p.name:
                    known_games.add(p.name)

        # Also discover any distinct game names recorded in the database
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT game_name FROM backups WHERE game_name IS NOT NULL AND game_name != ''")
                for row in cursor.fetchall():
                    known_games.add(row[0])
        except Exception:
            pass

        sorted_games = sorted(list(known_games))

        # Add individual game profiles
        active_target = self.settings.active_game.name if self.settings else ""
        selected_idx = 0

        for idx, g_name in enumerate(sorted_games):
            self.game_filter.addItem(f"🎮 {g_name}", g_name)
            if active_target and g_name == active_target:
                selected_idx = idx

        # Add 'All Games' option
        self.game_filter.addItem(f"🌐 {tr('filter_all_games')}", "all_games")

        # Restore selection or select active game
        if current_data:
            idx = self.game_filter.findData(current_data)
            if idx >= 0:
                self.game_filter.setCurrentIndex(idx)
            else:
                self.game_filter.setCurrentIndex(selected_idx)
        else:
            if sorted_games:
                self.game_filter.setCurrentIndex(selected_idx)
            else:
                self.game_filter.setCurrentIndex(0)

        self.game_filter.blockSignals(False)

    def set_active_game_filter(self, game_name: str, sync_global: bool = False) -> None:
        """Switch game filter to the specified game profile and refresh."""
        self._refresh_game_filter_options()
        self.game_filter.blockSignals(True)
        idx = self.game_filter.findData(game_name)
        if idx >= 0:
            self.game_filter.setCurrentIndex(idx)
        self.game_filter.blockSignals(False)
        self.refresh_records()
        if sync_global and game_name and game_name != "all_games":
            if self.settings and self.settings.active_game.name != game_name:
                self.request_switch_game.emit(game_name)

    def _on_game_filter_changed(self, index: int) -> None:
        if index < 0 or not hasattr(self, "game_filter"):
            return
        selected_data = self.game_filter.itemData(index)
        self.refresh_records()
        if selected_data and selected_data != "all_games":
            if self.settings and self.settings.active_game.name != selected_data:
                self.request_switch_game.emit(selected_data)

    def refresh_records(self) -> None:
        """Fetch records from SQLite database for selected game profile and status filter."""
        if hasattr(self, "game_filter") and self.game_filter.count() == 0:
            self._refresh_game_filter_options()

        status_val = self.status_filter.currentData() if hasattr(self, "status_filter") else "all"
        game_val = self.game_filter.currentData() if hasattr(self, "game_filter") else None
        game_query = None if (not game_val or game_val == "all_games") else game_val

        self._records = self.db.get_all_backups(game_name=game_query, status_filter=status_val)
        self._populate_table(self._records)

    def _populate_table(self, records: List[BackupRecord]) -> None:
        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            # Timestamp
            ts_str = rec.timestamp.replace("T", " ")[:19]
            self.table.setItem(row, 0, QTableWidgetItem(ts_str))

            # Filename
            file_item = QTableWidgetItem(rec.filename)
            file_item.setData(Qt.UserRole, rec)
            self.table.setItem(row, 1, file_item)

            # Size
            size_str = format_bytes(rec.size_bytes) if rec.size_bytes > 0 else "--"
            self.table.setItem(row, 2, QTableWidgetItem(size_str))

            # File Count
            self.table.setItem(row, 3, QTableWidgetItem(str(rec.file_count)))

            # Trigger
            trig_label = tr(f"trigger_{rec.trigger_type}")
            self.table.setItem(row, 4, QTableWidgetItem(trig_label))

            # Duration
            self.table.setItem(row, 5, QTableWidgetItem(f"{rec.duration_seconds:.1f}s"))

            # Verification
            ver_label = tr(rec.verification_status)
            ver_item = QTableWidgetItem(ver_label)
            if rec.verification_status == VerificationStatus.VERIFIED_OK.value:
                ver_item.setForeground(Qt.green)
            else:
                ver_item.setForeground(Qt.red)
            self.table.setItem(row, 6, ver_item)

            # Status
            stat_item = QTableWidgetItem(rec.status)
            if rec.status == BackupStatus.SUCCESS.value:
                stat_item.setForeground(Qt.green)
            else:
                stat_item.setForeground(Qt.red)
            self.table.setItem(row, 7, stat_item)

    def _apply_search_filter(self, text: str) -> None:
        query = text.lower().strip()
        if not query:
            self._populate_table(self._records)
            return

        filtered = [
            r for r in self._records
            if query in r.filename.lower() or query in r.timestamp.lower()
        ]
        self._populate_table(filtered)

    def _get_selected_record(self) -> Optional[BackupRecord]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        if item:
            return item.data(Qt.UserRole)
        return None

    def _on_inspect_clicked(self) -> None:
        rec = self._get_selected_record()
        if not rec:
            QMessageBox.information(self, "Select Backup", "Please select a backup archive to inspect.")
            return

        p = Path(rec.filepath)
        if not p.is_file():
            QMessageBox.critical(self, "Missing File", f"The archive file does not exist on disk:\n{rec.filepath}")
            return

        dlg = ZipInspectorDialog(rec.filepath, self.restore_mgr, self)
        dlg.exec()

    def _on_table_selection_changed(self) -> None:
        rec = self._get_selected_record()
        if not rec or not rec.filepath:
            self.lbl_preview.setText(tr("no_screenshot"))
            self.lbl_detail_filename.setText("")
            self.lbl_detail_meta.setText("Select a backup row from the table to inspect details.")
            self.lbl_preview_info.setText("")
            self.preview_pixmap = None
            return

        self.lbl_detail_filename.setText(rec.filename)
        dt_str = rec.timestamp.replace("T", " ")[:19]
        meta_text = (
            f"<b>Date:</b> {dt_str}<br>"
            f"<b>Size:</b> {format_bytes(rec.size_bytes)} ({rec.file_count} files)<br>"
            f"<b>Trigger:</b> {tr('trigger_' + rec.trigger_type)}<br>"
            f"<b>Integrity:</b> {tr(rec.verification_status)}"
        )
        self.lbl_detail_meta.setText(meta_text)
        self.lbl_preview_info.setText(f"{rec.filename}\n{format_bytes(rec.size_bytes)}")

        png_bytes = extract_screenshot_from_zip(rec.filepath)
        if png_bytes:
            pix = QPixmap()
            if pix.loadFromData(png_bytes):
                self.preview_pixmap = pix
                scaled = pix.scaled(self.lbl_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.lbl_preview.setPixmap(scaled)
                return

        self.lbl_preview.setText(tr("no_screenshot"))
        self.preview_pixmap = None

    def _on_compact_vault_clicked(self) -> None:
        rec = self._get_selected_record()
        target_path = Path(rec.filepath) if rec else None
        if not target_path or not target_path.is_file():
            QMessageBox.information(self, tr("btn_compact_vault"), "Please select a backup archive from the table to compact.")
            return

        ok, old_s, new_s = compact_vault(target_path)
        if ok:
            reclaimed = max(0, old_s - new_s)
            QMessageBox.information(
                self, tr("btn_compact_vault"),
                f"Compaction completed successfully!\nOld Size: {format_bytes(old_s)}\nNew Size: {format_bytes(new_s)}\nReclaimed Space: {format_bytes(reclaimed)}"
            )
            self.refresh_records()
        else:
            QMessageBox.warning(
                self, tr("btn_compact_vault"),
                "Compaction could not be completed or archive is already optimal."
            )

    def _on_reverify_clicked(self) -> None:
        rec = self._get_selected_record()
        if not rec:
            QMessageBox.information(self, "Select Backup", "Please select a backup archive to verify.")
            return

        is_valid, msg = verify_zip_archive(rec.filepath)
        new_status = VerificationStatus.VERIFIED_OK.value if is_valid else VerificationStatus.VERIFIED_FAIL.value
        self.db.update_backup_status(rec.id, rec.status, new_status, error_message=None if is_valid else msg)
        self.refresh_records()

        if is_valid:
            QMessageBox.information(self, "Integrity OK", f"Archive is valid and verified!\n{rec.filename}")
        else:
            QMessageBox.critical(self, "Integrity Error", f"Archive failed verification:\n{msg}")

    def _on_restore_clicked(self) -> None:
        rec = self._get_selected_record()
        if not rec:
            QMessageBox.information(self, "Select Backup", "Please select a backup archive to restore.")
            return
        self.request_restore_backup.emit(rec)

    def _on_open_folder_clicked(self) -> None:
        rec = self._get_selected_record()
        folder = Path(rec.filepath).parent if rec else None
        if not folder or not folder.exists():
            QMessageBox.warning(self, "Folder Missing", "Selected backup folder is not accessible.")
            return

        if sys.platform == "win32":
            os.startfile(str(folder))
        else:
            subprocess.run(["explorer", str(folder)])

    def _on_delete_clicked(self) -> None:
        rec = self._get_selected_record()
        if not rec:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to permanently delete archive:\n{rec.filename}?\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            p = Path(rec.filepath)
            if p.is_file():
                try:
                    p.unlink()
                except Exception as e:
                    QMessageBox.critical(self, "Delete Failed", f"Could not delete file from disk: {e}")
                    return

            self.db.delete_backup(rec.id)
            self.refresh_records()
