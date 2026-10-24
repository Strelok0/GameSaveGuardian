"""
Headless unit tests for PySide6 UI views, dialogs, and navigation.
Runs with QT_QPA_PLATFORM=offscreen.
"""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

# Ensure offscreen Qt platform for headless testing
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication, QMessageBox

from src.config import ConfigManager
from src.database import DatabaseManager
from src.models import AppSettings, BackupRecord, BackupStatus, GameConfig, GameProfile, VerificationStatus
from src.restore.restore_manager import RestoreManager
from src.ui.dashboard import DashboardPage
from src.ui.history_page import HistoryPage
from src.ui.main_window import MainWindow
from src.ui.restore_dialog import RestoreDialog
from src.ui.settings_page import SettingsPage
from src.ui.setup_wizard import SetupWizardDialog


class TestUIComponents(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create single QApplication instance for UI tests
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_ui_")
        self.base = Path(self.temp_dir)
        self.db_path = self.base / "ui_test.db"
        self.config_path = self.base / "ui_config.json"

        self.db = DatabaseManager(self.db_path)
        self.config_mgr = ConfigManager(self.config_path)
        self.settings = self.config_mgr.get_settings()
        self.restore_mgr = RestoreManager(self.db)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_dashboard_initialization_and_metrics_update(self):
        """Dashboard renders cleanly and updates metrics correctly."""
        dash = DashboardPage(self.settings)
        dash.update_metrics(
            protection_active=True,
            protection_paused=False,
            total_backups=5,
            total_bytes=1048576,
            next_scan_text="09:50"
        )
        self.assertEqual(dash.val_count.text(), "5")
        self.assertIn("MB", dash.val_storage.text())
        self.assertEqual(dash.val_next_scan.text(), "09:50")

    def test_history_page_table_population(self):
        """HistoryPage populates table rows with backup records."""
        rec = BackupRecord(
            game_name="The Witcher 3",
            filename="Witcher3_Test.zip",
            filepath=str(self.base / "Witcher3_Test.zip"),
            timestamp="2026-10-21T12:00:00",
            size_bytes=2048,
            file_count=3,
            status=BackupStatus.SUCCESS.value,
            verification_status=VerificationStatus.VERIFIED_OK.value
        )
        self.db.add_backup(rec)

        hist = HistoryPage(self.db, self.restore_mgr)
        hist.refresh_records()
        self.assertEqual(hist.table.rowCount(), 1)
        self.assertEqual(hist.table.item(0, 1).text(), "Witcher3_Test.zip")

    def test_history_page_filters_by_game_profile(self):
        """HistoryPage isolates backup records per game profile."""
        rec1 = BackupRecord(
            game_name="The Witcher 3",
            filename="Witcher3_1.zip",
            filepath=str(self.base / "Witcher3_1.zip"),
            timestamp="2026-10-21T12:00:00",
            size_bytes=1000,
            file_count=2,
            status=BackupStatus.SUCCESS.value
        )
        rec2 = BackupRecord(
            game_name="FalloutNV",
            filename="FalloutNV_1.zip",
            filepath=str(self.base / "FalloutNV_1.zip"),
            timestamp="2026-10-21T13:00:00",
            size_bytes=2000,
            file_count=3,
            status=BackupStatus.SUCCESS.value
        )
        self.db.add_backup(rec1)
        self.db.add_backup(rec2)

        hist = HistoryPage(self.db, self.restore_mgr, settings=self.settings)

        # 1. Filter to FalloutNV
        hist.set_active_game_filter("FalloutNV")
        self.assertEqual(hist.table.rowCount(), 1)
        self.assertEqual(hist.table.item(0, 1).text(), "FalloutNV_1.zip")

        # 2. Filter to The Witcher 3
        hist.set_active_game_filter("The Witcher 3")
        self.assertEqual(hist.table.rowCount(), 1)
        self.assertEqual(hist.table.item(0, 1).text(), "Witcher3_1.zip")

        # 3. All Games view
        hist.set_active_game_filter("all_games")
        self.assertEqual(hist.table.rowCount(), 2)

    @patch.object(QMessageBox, "information")
    def test_settings_page_input_and_persistence(self, mock_msg):
        """SettingsPage allows updating options and saving to disk."""
        settings_page = SettingsPage(self.config_mgr)
        settings_page.edit_game_name.setText("Witcher 3 Modded")
        settings_page.spin_interval.setValue(15)
        settings_page.chk_windows_startup.setChecked(True)
        settings_page.chk_auto_heal.setChecked(False)
        settings_page._save_changes()

        # Re-read from disk
        saved = self.config_mgr.load_config()
        self.assertEqual(saved.active_game.name, "Witcher 3 Modded")
        self.assertEqual(saved.scheduled_interval_minutes, 15)
        self.assertTrue(saved.start_with_windows)
        self.assertFalse(saved.auto_heal_missing_saves)

    def test_dashboard_auto_heal_button_and_signal(self):
        """DashboardPage provides a Recover Missing Saves button and signal."""
        dash = DashboardPage(self.settings)
        self.assertTrue(hasattr(dash, "btn_auto_heal"))
        received = []
        dash.request_auto_heal.connect(lambda: received.append(True))
        dash.btn_auto_heal.click()
        self.assertEqual(len(received), 1)

    def test_setup_wizard_dialog_instantiation(self):
        """SetupWizardDialog initializes with all steps."""
        wizard = SetupWizardDialog(self.db, self.settings)
        self.assertEqual(wizard.stack.count(), 5)
        self.assertEqual(wizard.current_step, 0)

    def test_game_profile_global_synchronization(self):
        """Switching active game globally synchronizes Dashboard, History, and Settings."""
        # 1. Add game profiles to config
        saves_dir = str(self.base / "saves")
        backups_dir = str(self.base / "backups")
        os.makedirs(saves_dir, exist_ok=True)
        os.makedirs(backups_dir, exist_ok=True)

        p1 = GameProfile(name="Witcher 3", save_directory=saves_dir, backup_directory=backups_dir)
        p2 = GameProfile(name="Cyberpunk 2077", save_directory=saves_dir, backup_directory=backups_dir)
        self.settings.games = [p1, p2]
        self.settings.active_game = p1
        self.config_mgr.save_config(self.settings)

        win = MainWindow(self.config_mgr, self.db)

        # Initially active: Witcher 3
        self.assertEqual(win.settings.active_game.name, "Witcher 3")
        self.assertIn("Witcher 3", win.page_dashboard.combo_games.currentText())

        # Switch to Cyberpunk 2077
        win._on_switch_active_game("Cyberpunk 2077")

        # Verify synchronized state across all views
        self.assertEqual(win.settings.active_game.name, "Cyberpunk 2077")
        self.assertIn("Cyberpunk 2077", win.page_dashboard.combo_games.currentText())
        self.assertEqual(win.page_history.game_filter.currentData(), "Cyberpunk 2077")
        self.assertEqual(win.page_settings.combo_profile_selector.currentData(), "Cyberpunk 2077")
        self.assertEqual(win.page_settings.edit_game_name.text(), "Cyberpunk 2077")

        win.close()

    @patch.object(QMessageBox, "information")
    def test_unified_backup_directory_option(self, mock_msg):
        """Unified backup directory option sets subfolder per game and persists."""
        settings_page = SettingsPage(self.config_mgr)
        unified_root = str(self.base / "All_Game_Backups")

        # Enable unified backup directory
        settings_page.chk_use_unified_backup.setChecked(True)
        settings_page.edit_backup_dir.setText(unified_root)
        settings_page._save_changes()

        saved = self.config_mgr.load_config()
        self.assertTrue(saved.use_unified_backup_dir)
        self.assertEqual(saved.unified_backup_directory, unified_root)

        # Verify games get dedicated subfolder inside unified directory
        fallout_dir = saved.get_backup_dir_for_game("FalloutNV")
        self.assertEqual(fallout_dir, Path(unified_root) / "FalloutNV")

        witcher_dir = saved.get_backup_dir_for_game("The Witcher 3")
        self.assertEqual(witcher_dir, Path(unified_root) / "The Witcher 3")

    def test_witcher_profile_auto_deduplication_on_load(self):
        """Deduplicates and merges 'The Witcher 3' into 'The Witcher 3: Wild Hunt' seamlessly."""
        raw_data = {
            "active_game": {"name": "The Witcher 3", "save_directory": "C:/Saves/W3", "backup_directory": "C:/Backups/W3"},
            "games": [
                {"name": "The Witcher 3: Wild Hunt", "save_directory": "", "backup_directory": ""},
                {"name": "The Witcher 3", "save_directory": "C:/Saves/W3", "backup_directory": "C:/Backups/W3"},
                {"name": "Elden Ring", "save_directory": "C:/Saves/ER", "backup_directory": "C:/Backups/ER"},
            ]
        }
        loaded = AppSettings.from_dict(raw_data)
        game_names = [g.name for g in loaded.games]
        self.assertIn("The Witcher 3: Wild Hunt", game_names)
        self.assertNotIn("The Witcher 3", game_names)
        self.assertEqual(len(game_names), 2)
        # Check that save_directory was merged into The Witcher 3: Wild Hunt
        w3_prof = next(g for g in loaded.games if g.name == "The Witcher 3: Wild Hunt")
        self.assertEqual(w3_prof.save_directory, "C:/Saves/W3")
        self.assertEqual(loaded.active_game.name, "The Witcher 3: Wild Hunt")

    @patch.object(QMessageBox, "information")
    @patch.object(QMessageBox, "question", return_value=QMessageBox.Yes)
    def test_settings_page_delete_profile(self, mock_question, mock_info):
        """SettingsPage allows removing a selected profile and updates selection."""
        p1 = GameProfile(name="The Witcher 3: Wild Hunt", save_directory=str(self.base / "w3"))
        p2 = GameProfile(name="Cyberpunk 2077", save_directory=str(self.base / "cp"))
        self.settings.games = [p1, p2]
        self.settings.active_game = p1
        self.config_mgr.save_config(self.settings)

        settings_page = SettingsPage(self.config_mgr)
        self.assertEqual(len(settings_page.settings.games), 2)
        self.assertTrue(settings_page.btn_delete_profile.isEnabled())

        # Select Cyberpunk 2077 to delete
        idx = settings_page.combo_profile_selector.findData("Cyberpunk 2077")
        settings_page.combo_profile_selector.setCurrentIndex(idx)

        # Trigger delete
        settings_page._on_delete_profile_clicked()

        self.assertEqual(len(settings_page.settings.games), 1)
        self.assertEqual(settings_page.settings.games[0].name, "The Witcher 3: Wild Hunt")
        self.assertEqual(settings_page.settings.active_game.name, "The Witcher 3: Wild Hunt")
        # With only 1 profile remaining, delete button should be disabled
        self.assertFalse(settings_page.btn_delete_profile.isEnabled())

    def test_multi_game_background_realtime_auto_switch_and_backup(self):
        """
        When FalloutNV is the active profile and the user saves in Witcher 3,
        the realtime engine automatically detects Witcher 3, switches the active profile,
        and creates a backup for Witcher 3.
        """
        w3_saves = self.base / "WitcherSaves"
        w3_backups = self.base / "WitcherBackups"
        fnv_saves = self.base / "FalloutSaves"
        fnv_backups = self.base / "FalloutBackups"
        for d in (w3_saves, w3_backups, fnv_saves, fnv_backups):
            d.mkdir(parents=True, exist_ok=True)

        p_fnv = GameProfile(name="FalloutNV", save_directory=str(fnv_saves), backup_directory=str(fnv_backups))
        p_w3 = GameProfile(name="The Witcher 3: Wild Hunt", save_directory=str(w3_saves), backup_directory=str(w3_backups))
        self.settings.games = [p_fnv, p_w3]
        self.settings.active_game = p_fnv
        self.settings.backup_mode = "realtime"
        self.config_mgr.save_config(self.settings)

        win = MainWindow(self.config_mgr, self.db)
        win._start_protection()
        self.assertEqual(win.settings.active_game.name, "FalloutNV")

        # Simulate user writing a new save file in Witcher 3
        (w3_saves / "AutoSave_001.sav").write_bytes(b"WITCHER_SAVE_DATA_BYTES")

        # Simulate realtime detection event arriving for 'The Witcher 3: Wild Hunt'
        win._handle_realtime_change_on_gui_thread("The Witcher 3: Wild Hunt")

        # Ensure worker thread completes
        if win.worker_thread:
            win.worker_thread.wait(5000)

        # Assert active game automatically switched to Witcher 3
        self.assertEqual(win.settings.active_game.name, "The Witcher 3: Wild Hunt")
        self.assertIn("The Witcher 3: Wild Hunt", win.page_dashboard.game_title_lbl.text())

        # Assert backup was created for Witcher 3 in its dedicated directory
        w3_backups_list = list(w3_backups.glob("*.zip"))
        self.assertEqual(len(w3_backups_list), 1)

        # Assert database recorded backup under The Witcher 3: Wild Hunt
        w3_records = self.db.get_all_backups("The Witcher 3: Wild Hunt")
        self.assertEqual(len(w3_records), 1)
        self.assertEqual(w3_records[0].game_name, "The Witcher 3: Wild Hunt")

        win.close()


if __name__ == "__main__":
    unittest.main()
