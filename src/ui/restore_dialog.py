"""
Interactive Safe Restore Dialog for Game Save Guardian.
Offers inspection, preview of changes, staging folder mode, and pre-restore safety backups.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.backup.zip_writer import extract_screenshot_from_zip
from src.models import BackupRecord
from src.restore.restore_manager import RestoreManager, RestorePreview
from src.utils.i18n import tr
from src.utils.paths import format_bytes


class RestoreWorkerThread(QThread):
    """Background worker thread for safe restoration without freezing the GUI."""

    progress = Signal(int, int, str)
    finished = Signal(bool, str, object)

    def __init__(
        self,
        restore_mgr: RestoreManager,
        zip_path: Path | str,
        target_dir: Path | str,
        is_direct_restore: bool,
        backup_dir_for_safety: Optional[Path | str]
    ):
        super().__init__()
        self.restore_mgr = restore_mgr
        self.zip_path = zip_path
        self.target_dir = target_dir
        self.is_direct_restore = is_direct_restore
        self.backup_dir_for_safety = backup_dir_for_safety

    def run(self):
        def _prog(cur: int, tot: int, text: str):
            self.progress.emit(cur, tot, text)

        ok, msg, safety_path = self.restore_mgr.execute_restore(
            zip_path=self.zip_path,
            target_dir=self.target_dir,
            is_direct_restore=self.is_direct_restore,
            backup_dir_for_safety=self.backup_dir_for_safety,
            progress_callback=_prog
        )
        self.finished.emit(ok, msg, safety_path)


class RestoreDialog(QDialog):
    """Wizard-style safe restoration modal."""

    restore_completed = Signal(str)

    def __init__(
        self,
        record: BackupRecord,
        restore_manager: RestoreManager,
        active_save_dir: str,
        backup_dir: str,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.record = record
        self.restore_mgr = restore_manager
        self.active_save_dir = active_save_dir
        self.backup_dir = backup_dir
        self.preview_data: Optional[RestorePreview] = None
        self.cached_file_list: Optional[List[dict]] = None
        self._screenshot_loaded: bool = False
        self._worker: Optional[RestoreWorkerThread] = None

        self.setWindowTitle(tr("restore_title"))
        self.resize(760, 660)
        self.setMinimumSize(680, 560)
        self.setModal(True)
        self._init_ui()
        self._load_preview()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header Info Card
        info_card = QFrame()
        info_card.setProperty("class", "metric-card")
        ic_layout = QHBoxLayout(info_card)
        ic_layout.setContentsMargins(12, 12, 12, 12)
        ic_layout.setSpacing(16)

        text_box = QVBoxLayout()
        title_lbl = QLabel(f"<b>Restore Archive:</b> {self.record.filename}")
        title_lbl.setStyleSheet("font-size: 14px; color: #10b981;")
        text_box.addWidget(title_lbl)

        details = (
            f"<b>Archive Date:</b> {self.record.timestamp.replace('T', ' ')[:19]}<br>"
            f"<b>Size:</b> {format_bytes(self.record.size_bytes)} | "
            f"<b>Files:</b> {self.record.file_count}"
        )
        text_box.addWidget(QLabel(details))
        ic_layout.addLayout(text_box, 1)

        # Screenshot Thumbnail Preview
        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(140, 80)
        self.lbl_preview.setStyleSheet(
            "border: 1px solid #334155; border-radius: 6px; background-color: #0f172a; color: #94a3b8; font-size: 11px;"
        )
        self.lbl_preview.setAlignment(Qt.AlignCenter)
        self.preview_pixmap: Optional[QPixmap] = None
        ic_layout.addWidget(self.lbl_preview)

        layout.addWidget(info_card)

        # Restore Mode Selection
        layout.addWidget(QLabel(f"<b>{tr('restore_notice')}</b>"))

        self.radio_staging = QRadioButton(tr("restore_mode_staging"))
        self.radio_staging.setChecked(True)
        layout.addWidget(self.radio_staging)

        # Staging folder selection row
        self.staging_box = QWidget()
        s_layout = QHBoxLayout(self.staging_box)
        s_layout.setContentsMargins(20, 0, 0, 0)
        default_staging = str(Path.home() / "Desktop" / f"Restored_Saves_{self.record.filename[:-4]}")
        self.staging_edit = QLineEdit(default_staging)
        browse_btn = QPushButton(tr("btn_browse"))
        browse_btn.setProperty("class", "secondary-btn")
        browse_btn.clicked.connect(self._browse_staging_dir)
        s_layout.addWidget(self.staging_edit)
        s_layout.addWidget(browse_btn)
        layout.addWidget(self.staging_box)

        self.radio_direct = QRadioButton(tr("restore_mode_direct"))
        layout.addWidget(self.radio_direct)

        self.safety_note_lbl = QLabel(tr("restore_safety_backup_notice"))
        self.safety_note_lbl.setStyleSheet("color: #34d399; font-size: 11px; margin-left: 20px;")
        layout.addWidget(self.safety_note_lbl)

        btn_group = QButtonGroup(self)
        btn_group.addButton(self.radio_staging)
        btn_group.addButton(self.radio_direct)
        btn_group.buttonToggled.connect(self._on_mode_toggled)

        # Files Preview Table Header & Table
        self.table_header_lbl = QLabel("<b>Files to be Restored:</b>")
        layout.addWidget(self.table_header_lbl)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["Save File", "Action", "Status"])
        self.preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.preview_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.preview_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.preview_table.setMinimumHeight(180)
        layout.addWidget(self.preview_table, 1)

        # Restoration Progress Display (Hidden until restore is triggered)
        self.progress_container = QWidget()
        p_layout = QVBoxLayout(self.progress_container)
        p_layout.setContentsMargins(0, 4, 0, 4)
        p_layout.setSpacing(4)

        prog_header = QHBoxLayout()
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 500;")
        self.progress_pct_lbl = QLabel("0%")
        self.progress_pct_lbl.setStyleSheet("color: #10b981; font-size: 13px; font-weight: 700;")
        prog_header.addWidget(self.progress_lbl, 1)
        prog_header.addWidget(self.progress_pct_lbl, 0, Qt.AlignRight)
        p_layout.addLayout(prog_header)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(14)
        self.progress_bar.setTextVisible(False)
        p_layout.addWidget(self.progress_bar)

        self.progress_container.setVisible(False)
        layout.addWidget(self.progress_container)

        # Bottom Buttons
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()

        self.cancel_btn = QPushButton(tr("btn_cancel"))
        self.cancel_btn.setProperty("class", "secondary-btn")
        self.cancel_btn.clicked.connect(self.reject)
        btn_bar.addWidget(self.cancel_btn)

        self.restore_btn = QPushButton(tr("btn_restore"))
        self.restore_btn.setProperty("class", "primary-btn")
        self.restore_btn.clicked.connect(self._execute_restore)
        btn_bar.addWidget(self.restore_btn)

        layout.addLayout(btn_bar)

    def _on_mode_toggled(self, button=None, checked: bool = True) -> None:
        if not checked:
            return
        is_staging = self.radio_staging.isChecked()
        self.staging_box.setVisible(is_staging)
        self._load_preview()

    def _browse_staging_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Restore Folder", self.staging_edit.text())
        if folder:
            self.staging_edit.setText(folder)
            self._load_preview()

    def _load_preview(self) -> None:
        # Load screenshot preview thumbnail in memory once
        if not self._screenshot_loaded:
            self._screenshot_loaded = True
            png_bytes = extract_screenshot_from_zip(self.record.filepath)
            if png_bytes:
                pix = QPixmap()
                if pix.loadFromData(png_bytes):
                    self.preview_pixmap = pix
                    scaled = pix.scaled(self.lbl_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.lbl_preview.setPixmap(scaled)
                else:
                    self.lbl_preview.setText(tr("no_screenshot"))
            else:
                self.lbl_preview.setText(tr("no_screenshot"))

        target_dir = self.staging_edit.text().strip() if self.radio_staging.isChecked() else self.active_save_dir
        if not target_dir:
            return

        # Inspect archive once and cache results in memory
        if self.cached_file_list is None:
            ok, file_list, err = self.restore_mgr.inspect_archive(self.record.filepath)
            if ok:
                self.cached_file_list = file_list
            else:
                return

        ok, preview, err = self.restore_mgr.generate_preview(
            self.record.filepath,
            target_dir,
            cached_file_list=self.cached_file_list
        )
        if not ok or not preview:
            return

        self.preview_data = preview
        total_rows = len(preview.files_to_add) + len(preview.files_to_replace)
        self.table_header_lbl.setText(f"<b>Files to be Restored ({total_rows:,} total saves):</b>")

        # Cap rendered rows to prevent UI lag on massive archives
        MAX_DISPLAY = 200
        disp_replace = preview.files_to_replace[:MAX_DISPLAY]
        remaining = MAX_DISPLAY - len(disp_replace)
        disp_add = preview.files_to_add[:max(0, remaining)]
        has_more = total_rows > MAX_DISPLAY
        row_count = len(disp_replace) + len(disp_add) + (1 if has_more else 0)

        self.preview_table.setUpdatesEnabled(False)
        self.preview_table.setRowCount(row_count)

        row = 0
        for f in disp_replace:
            self.preview_table.setItem(row, 0, QTableWidgetItem(f))
            self.preview_table.setItem(row, 1, QTableWidgetItem("Replace Existing File"))
            item = QTableWidgetItem("Will Overwrite")
            item.setForeground(Qt.yellow)
            self.preview_table.setItem(row, 2, item)
            row += 1

        for f in disp_add:
            self.preview_table.setItem(row, 0, QTableWidgetItem(f))
            self.preview_table.setItem(row, 1, QTableWidgetItem("New Save File"))
            item = QTableWidgetItem("Will Add")
            item.setForeground(Qt.green)
            self.preview_table.setItem(row, 2, item)
            row += 1

        if has_more:
            hidden = total_rows - (len(disp_replace) + len(disp_add))
            info_item = QTableWidgetItem(f"📁 ... and {hidden:,} more save files")
            info_item.setForeground(Qt.cyan)
            self.preview_table.setItem(row, 0, info_item)
            self.preview_table.setItem(row, 1, QTableWidgetItem(f"Total: {total_rows:,} files"))
            self.preview_table.setItem(row, 2, QTableWidgetItem("All Included"))

        self.preview_table.setUpdatesEnabled(True)

    def _execute_restore(self) -> None:
        is_direct = self.radio_direct.isChecked()
        target_dir = self.active_save_dir if is_direct else self.staging_edit.text().strip()

        if is_direct:
            reply = QMessageBox.warning(
                self,
                "Confirm Direct Restore",
                "You have chosen to directly restore and replace your current save files!\n\n"
                "A pre-restore safety backup will be created first.\n\n"
                "Are you sure you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        # Disable interactive controls while worker thread executes
        self.restore_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.radio_staging.setEnabled(False)
        self.radio_direct.setEnabled(False)

        # Show progress feedback
        self.progress_container.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_lbl.setText("Preparing safe restoration...")
        self.progress_pct_lbl.setText("0%")

        self._worker = RestoreWorkerThread(
            restore_mgr=self.restore_mgr,
            zip_path=self.record.filepath,
            target_dir=target_dir,
            is_direct_restore=is_direct,
            backup_dir_for_safety=self.backup_dir
        )
        self._worker.progress.connect(self._on_restore_progress)
        self._worker.finished.connect(lambda ok, msg, sp: self._on_restore_finished(ok, msg, sp, target_dir))
        self._worker.start()

    def _on_restore_progress(self, current: int, total: int, status: str) -> None:
        pct = int(current / max(1, total) * 100)
        self.progress_bar.setValue(pct)
        self.progress_pct_lbl.setText(f"{pct}%")
        self.progress_lbl.setText(status)

    def _on_restore_finished(self, ok: bool, msg: str, safety_path: Optional[Path], target_dir: str) -> None:
        self.progress_container.setVisible(False)
        self.restore_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self.radio_staging.setEnabled(True)
        self.radio_direct.setEnabled(True)

        if ok:
            succ_text = f"{tr('restore_success')}\nTarget: {target_dir}"
            if safety_path:
                succ_text += f"\n\nPre-restore safety backup created at:\n{safety_path.name}"
            QMessageBox.information(self, "Restore Completed", succ_text)
            self.restore_completed.emit(str(target_dir))
            self.accept()
        else:
            QMessageBox.critical(self, "Restore Failed", f"{tr('restore_failed')}\n\nError: {msg}")
