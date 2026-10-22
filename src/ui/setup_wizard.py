"""
First-Run Setup Wizard dialog for Game Save Guardian.
Guides new users through game selection, save detection, destination configuration, and test backup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.backup.backup_manager import BackupManager
from src.backup.integrity import build_manifest
from src.database import DatabaseManager
from src.models import AppSettings, BackupTrigger, GameConfig, GameProfile, ProtectionMode
from src.utils.i18n import tr
from src.utils.logging_config import get_logger
from src.utils.paths import (
    auto_detect_all_games,
    detect_witcher_3_save_paths,
    get_default_backup_dir,
    is_path_overlapping_or_recursive,
    is_same_physical_disk,
)

logger = get_logger()


class SetupWizardDialog(QDialog):
    """Multi-step setup wizard for first-run configuration."""

    setup_finished = Signal(AppSettings)

    def __init__(self, db_manager: DatabaseManager, settings: AppSettings, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.db = db_manager
        self.settings = settings
        self.setWindowTitle(tr("wizard_title"))
        self.resize(720, 540)
        self.setMinimumSize(640, 480)
        self.setModal(True)

        self.current_step = 0
        self.detected_games_map: dict[str, Path] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)

        # Header Title
        self.title_lbl = QLabel(tr("wizard_title"))
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #10b981;")
        main_layout.addWidget(self.title_lbl)

        # Stacked Pages
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack, 1)

        # Build Wizard Pages
        self.stack.addWidget(self._create_step1_game())
        self.stack.addWidget(self._create_step2_source())
        self.stack.addWidget(self._create_step3_dest())
        self.stack.addWidget(self._create_step4_strategy())
        self.stack.addWidget(self._create_step5_test_review())

        # Bottom Navigation Buttons
        btn_bar = QHBoxLayout()
        self.back_btn = QPushButton(tr("btn_back"))
        self.back_btn.setProperty("class", "secondary-btn")
        self.back_btn.clicked.connect(self._on_back_clicked)
        self.back_btn.setEnabled(False)

        self.next_btn = QPushButton(tr("btn_next"))
        self.next_btn.setProperty("class", "primary-btn")
        self.next_btn.clicked.connect(self._on_next_clicked)

        btn_bar.addWidget(self.back_btn)
        btn_bar.addStretch()
        btn_bar.addWidget(self.next_btn)
        main_layout.addLayout(btn_bar)

    def _create_step1_game(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        desc = QLabel(tr("wizard_step1_desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        lbl = QLabel(tr("field_game_name") + ":")
        layout.addWidget(lbl)

        self.game_combo = QComboBox()
        self.game_combo.addItem("The Witcher 3: Wild Hunt")
        self.game_combo.addItem("Custom Game...")
        self.game_combo.currentTextChanged.connect(self._on_game_selection_changed)
        layout.addWidget(self.game_combo)

        btn_detect_all = QPushButton(tr("btn_detect_all_games"))
        btn_detect_all.setProperty("class", "secondary-btn")
        btn_detect_all.clicked.connect(self._run_auto_detect_all_games)
        layout.addWidget(btn_detect_all)

        self.custom_game_edit = QLineEdit()
        self.custom_game_edit.setPlaceholderText("Enter game title")
        self.custom_game_edit.setVisible(False)
        layout.addWidget(self.custom_game_edit)

        layout.addStretch()
        return page

    def _create_step2_source(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        desc = QLabel(tr("wizard_step2_desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        help_lbl = QLabel(tr("wizard_witcher_help"))
        help_lbl.setStyleSheet("color: #94a3b8; font-style: italic;")
        layout.addWidget(help_lbl)

        h_box = QHBoxLayout()
        self.source_dir_edit = QLineEdit(self.settings.active_game.save_directory)
        browse_btn = QPushButton(tr("btn_browse"))
        browse_btn.setProperty("class", "secondary-btn")
        browse_btn.clicked.connect(self._browse_source_dir)
        h_box.addWidget(self.source_dir_edit)
        h_box.addWidget(browse_btn)
        layout.addLayout(h_box)

        detect_bar = QHBoxLayout()
        self.auto_detect_btn = QPushButton(tr("btn_auto_detect"))
        self.auto_detect_btn.setProperty("class", "primary-btn")
        self.auto_detect_btn.clicked.connect(self._run_auto_detect_witcher3)

        self.auto_detect_all_btn = QPushButton(tr("btn_detect_all_games"))
        self.auto_detect_all_btn.setProperty("class", "secondary-btn")
        self.auto_detect_all_btn.clicked.connect(self._run_auto_detect_all_games)

        detect_bar.addWidget(self.auto_detect_btn)
        detect_bar.addWidget(self.auto_detect_all_btn)
        layout.addLayout(detect_bar)

        self.detect_status_lbl = QLabel("")
        self.detect_status_lbl.setWordWrap(True)
        layout.addWidget(self.detect_status_lbl)

        layout.addStretch()
        return page

    def _create_step3_dest(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        desc = QLabel(tr("wizard_step3_desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        default_dest = str(get_default_backup_dir(self.settings.active_game.name))
        h_box = QHBoxLayout()
        self.dest_dir_edit = QLineEdit(self.settings.active_game.backup_directory or default_dest)
        browse_btn = QPushButton(tr("btn_browse"))
        browse_btn.setProperty("class", "secondary-btn")
        browse_btn.clicked.connect(self._browse_dest_dir)
        h_box.addWidget(self.dest_dir_edit)
        h_box.addWidget(browse_btn)
        layout.addLayout(h_box)

        self.disk_warning_lbl = QLabel("")
        self.disk_warning_lbl.setStyleSheet("color: #f59e0b; font-size: 11px;")
        self.disk_warning_lbl.setWordWrap(True)
        layout.addWidget(self.disk_warning_lbl)

        layout.addStretch()
        return page

    def _create_step4_strategy(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        desc = QLabel(tr("wizard_step4_desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addWidget(QLabel(tr("field_trigger_mode") + ":"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("mode_hybrid"), ProtectionMode.HYBRID.value)
        self.mode_combo.addItem(tr("mode_realtime"), ProtectionMode.REALTIME.value)
        self.mode_combo.addItem(tr("mode_scheduled"), ProtectionMode.SCHEDULED.value)
        layout.addWidget(self.mode_combo)

        layout.addWidget(QLabel(tr("field_archive_mode") + ":"))
        self.archive_mode_combo = QComboBox()
        self.archive_mode_combo.addItem(tr("archive_mode_single_vault"), "single_vault")
        self.archive_mode_combo.addItem(tr("archive_mode_snapshots"), "snapshots")
        layout.addWidget(self.archive_mode_combo)

        layout.addWidget(QLabel(tr("field_sched_interval") + ":"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 1440)
        self.interval_spin.setValue(self.settings.scheduled_interval_minutes)
        layout.addWidget(self.interval_spin)

        layout.addWidget(QLabel(tr("field_retention_max_count") + ":"))
        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 500)
        self.retention_spin.setValue(self.settings.retention.max_count)
        layout.addWidget(self.retention_spin)

        layout.addStretch()
        return page

    def _create_step5_test_review(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(12)

        desc = QLabel(tr("wizard_step5_desc"))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.review_box = QFrame()
        self.review_box.setProperty("class", "metric-card")
        box_layout = QVBoxLayout(self.review_box)
        self.review_text_lbl = QLabel("")
        self.review_text_lbl.setWordWrap(True)
        box_layout.addWidget(self.review_text_lbl)
        layout.addWidget(self.review_box)

        self.test_btn = QPushButton(tr("wizard_test_backup_btn"))
        self.test_btn.setProperty("class", "primary-btn")
        self.test_btn.clicked.connect(self._run_test_backup)
        layout.addWidget(self.test_btn)

        self.test_result_lbl = QLabel("")
        self.test_result_lbl.setWordWrap(True)
        layout.addWidget(self.test_result_lbl)

        layout.addStretch()
        return page

    def _on_game_selection_changed(self, text: str) -> None:
        is_custom = "Custom" in text
        self.custom_game_edit.setVisible(is_custom)
        if not is_custom:
            self.settings.active_game.name = text
            if text in self.detected_games_map:
                save_p = self.detected_games_map[text]
                self.source_dir_edit.setText(str(save_p))
                self.dest_dir_edit.setText(str(get_default_backup_dir(text)))
            else:
                # Auto update default destination
                if not self.dest_dir_edit.text() or self.dest_dir_edit.text() == str(get_default_backup_dir(self.settings.active_game.name)):
                    self.dest_dir_edit.setText(str(get_default_backup_dir(text)))

    def _browse_source_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("wizard_step2_title"), self.source_dir_edit.text())
        if folder:
            self.source_dir_edit.setText(folder)
            self.detect_status_lbl.setText("")

    def _browse_dest_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("wizard_step3_title"), self.dest_dir_edit.text())
        if folder:
            self.dest_dir_edit.setText(folder)
            self._check_disk_warning()

    def _run_auto_detect_witcher3(self) -> None:
        paths = detect_witcher_3_save_paths()
        if paths:
            found = paths[0]
            self.source_dir_edit.setText(str(found))
            self.detect_status_lbl.setText(f"<font color='#10b981'><b>{tr('wizard_witcher_auto_found')}</b><br>{found}</font>")
        else:
            self.detect_status_lbl.setText(f"<font color='#f59e0b'>{tr('wizard_witcher_not_found')}</font>")

    def _run_auto_detect_all_games(self) -> None:
        games = auto_detect_all_games()
        if not games:
            QMessageBox.information(self, "Auto-Detect Games", "No game save directories were automatically discovered.")
            return

        self.detected_games_map = games
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        for name in games.keys():
            self.game_combo.addItem(name)
        self.game_combo.addItem("The Witcher 3: Wild Hunt")
        self.game_combo.addItem("Custom Game...")
        self.game_combo.blockSignals(False)

        first_name = list(games.keys())[0]
        first_path = games[first_name]
        self.game_combo.setCurrentText(first_name)
        self.source_dir_edit.setText(str(first_path))
        self.dest_dir_edit.setText(str(get_default_backup_dir(first_name)))
        self.detect_status_lbl.setText(f"<font color='#10b981'><b>Auto-detected {len(games)} game profile(s):</b><br>{first_name} -> {first_path}</font>")

        QMessageBox.information(
            self, "Auto-Detect Games",
            f"Successfully scanned and identified {len(games)} game profile(s)!"
        )

    def _check_disk_warning(self) -> None:
        src = self.source_dir_edit.text().strip()
        dest = self.dest_dir_edit.text().strip()
        if src and dest and is_same_physical_disk(src, dest):
            self.disk_warning_lbl.setText(tr("msg_dest_same_disk"))
        else:
            self.disk_warning_lbl.setText("")

    def _update_review_text(self) -> None:
        src = self.source_dir_edit.text().strip()
        dest = self.dest_dir_edit.text().strip()
        game = self.custom_game_edit.text().strip() if self.custom_game_edit.isVisible() else self.game_combo.currentText()
        mode_text = self.mode_combo.currentText()

        info = (
            f"<b>{tr('card_current_game')}:</b> {game}<br>"
            f"<b>{tr('card_save_dir')}:</b> {src}<br>"
            f"<b>{tr('card_backup_dir')}:</b> {dest}<br>"
            f"<b>{tr('card_mode')}:</b> {mode_text}<br>"
            f"<b>{tr('field_sched_interval')}:</b> {self.interval_spin.value()} min<br>"
            f"<b>{tr('field_retention_max_count')}:</b> {self.retention_spin.value()}"
        )
        self.review_text_lbl.setText(info)

    def _run_test_backup(self) -> None:
        self._apply_inputs_to_settings()
        mgr = BackupManager(self.db, self.settings)
        self.test_btn.setEnabled(False)
        self.test_result_lbl.setText(tr("status_backing_up"))

        try:
            ok, msg, record = mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
            if ok and record:
                self.test_result_lbl.setText(f"<font color='#10b981'><b>{tr('wizard_test_backup_success')}</b><br>{record.filename}</font>")
            else:
                self.test_result_lbl.setText(f"<font color='#ef4444'><b>{tr('wizard_test_backup_failed')}</b><br>{msg}</font>")
        except Exception as e:
            self.test_result_lbl.setText(f"<font color='#ef4444'>Error: {e}</font>")
        finally:
            self.test_btn.setEnabled(True)

    def _apply_inputs_to_settings(self) -> None:
        game_name = self.custom_game_edit.text().strip() if self.custom_game_edit.isVisible() else self.game_combo.currentText()
        self.settings.active_game.name = game_name
        self.settings.active_game.save_directory = self.source_dir_edit.text().strip()
        self.settings.active_game.backup_directory = self.dest_dir_edit.text().strip()
        self.settings.backup_mode = self.mode_combo.currentData()
        self.settings.archive_mode = self.archive_mode_combo.currentData()
        self.settings.scheduled_interval_minutes = self.interval_spin.value()
        self.settings.retention.max_count = self.retention_spin.value()

        # Save all auto-detected games to settings profile catalog
        if hasattr(self, "detected_games_map"):
            for name, path in self.detected_games_map.items():
                self.settings.add_game_profile(
                    GameProfile(
                        name=name,
                        save_directory=str(path),
                        backup_directory=str(get_default_backup_dir(name))
                    )
                )

    def _on_next_clicked(self) -> None:
        if self.current_step == 0:
            # Step 1: Game
            self.current_step += 1
            self.stack.setCurrentIndex(self.current_step)
            self.back_btn.setEnabled(True)
            # If Witcher 3, try proactive auto detect
            if "Witcher" in self.game_combo.currentText() and not self.source_dir_edit.text():
                self._run_auto_detect_witcher3()

        elif self.current_step == 1:
            # Step 2: Source validation
            src = self.source_dir_edit.text().strip()
            if not src or not Path(src).is_dir():
                QMessageBox.warning(self, "Invalid Save Directory", "Please select an existing directory where your game save files are stored.")
                return
            self.current_step += 1
            self.stack.setCurrentIndex(self.current_step)
            self._check_disk_warning()

        elif self.current_step == 2:
            # Step 3: Dest validation & recursion check
            src = self.source_dir_edit.text().strip()
            dest = self.dest_dir_edit.text().strip()
            if not dest:
                QMessageBox.warning(self, "Invalid Destination", "Please choose a backup destination folder.")
                return

            overlap, reason = is_path_overlapping_or_recursive(src, dest)
            if overlap:
                QMessageBox.critical(self, "Recursive Path Error", reason)
                return

            self.current_step += 1
            self.stack.setCurrentIndex(self.current_step)

        elif self.current_step == 3:
            # Step 4: Strategy
            self.current_step += 1
            self.stack.setCurrentIndex(self.current_step)
            self._update_review_text()
            self.next_btn.setText(tr("btn_finish"))

        elif self.current_step == 4:
            # Step 5: Finish
            self._apply_inputs_to_settings()
            self.settings.first_run_completed = True
            self.setup_finished.emit(self.settings)
            self.accept()

    def _on_back_clicked(self) -> None:
        if self.current_step > 0:
            self.current_step -= 1
            self.stack.setCurrentIndex(self.current_step)
            self.next_btn.setText(tr("btn_next"))
            if self.current_step == 0:
                self.back_btn.setEnabled(False)
