"""
Comprehensive unit test suite for Game Save Guardian architectural optimizations.
Tests the 8 core performance, reliability, and UX enhancements:
1. Crash-Safe Single Vault Append Journaling (.vault_journal rollback)
2. Automated Vault Compaction for dead/duplicate space reclamation
3. Zero-compression bypass (ZIP_STORED) for pre-compressed assets (.png)
4. PySide6 RAM trimming & UI timer suspension on hide/show
5. In-memory screenshot preview extraction without disk I/O
6. Silent Gaming Mode & notification suppression
7. Headless / CLI silent minimized startup flag (--minimized)
8. Multi-game profile detection & ultra-fast bounded scanning (<50ms)
"""

import base64
import os
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest
import zipfile

# Set offscreen platform for headless Qt UI execution
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

from src.app import create_app_window
from src.backup.backup_manager import BackupManager
from src.backup.integrity import build_manifest
from src.backup.zip_writer import (
    VAULT_JOURNAL_MAGIC,
    ZipWriter,
    calculate_vault_dead_space,
    compact_vault,
    extract_screenshot_from_zip,
    recover_vault_from_journal,
    should_store_without_compression,
)
from src.config import ConfigManager
from src.database import DatabaseManager
from src.models import AppSettings, BackupRecord, BackupStatus, BackupTrigger, GameConfig, VerificationStatus
from src.ui.history_page import HistoryPage
from src.ui.main_window import MainWindow
from src.ui.restore_dialog import RestoreDialog
from src.utils.paths import auto_detect_all_games

# Standard 1x1 transparent PNG image data (valid PNG bytes)
VALID_1X1_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class TestOptimizations(unittest.TestCase):
    """Automated benchmark and verification suite for Game Save Guardian optimizations."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_opt_")
        self.base = Path(self.temp_dir)
        self.db_path = self.base / "opt_test.db"
        self.config_path = self.base / "opt_config.json"

        self.db = DatabaseManager(self.db_path)
        self.config_mgr = ConfigManager(self.config_path)
        self.settings = self.config_mgr.get_settings()

        self.saves_dir = self.base / "saves"
        self.backups_dir = self.base / "backups"
        self.saves_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        self.settings.active_game.save_directory = str(self.saves_dir)
        self.settings.active_game.backup_directory = str(self.backups_dir)
        self.settings.archive_mode = "single_vault"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vault_journal_crash_recovery(self):
        """
        Test 1: Vault append crash journaling rollback.
        Simulate an interrupted append with trailing corrupt bytes and an active
        .vault_journal sidecar. Asserts 100% valid restored state and journal cleanup.
        """
        vault_path = self.backups_dir / "CrashGame_Backup.zip"

        # Create valid initial archive with 1 save file
        file1 = self.saves_dir / "Slot1.sav"
        file1.write_text("INITIAL_VALID_SAVE_DATA_CONTENT", encoding="utf-8")
        manifest = build_manifest(self.saves_dir)
        writer = ZipWriter()
        tmp_p, final_p = writer.create_snapshot_archive(self.saves_dir, self.backups_dir, manifest, "CrashGame_Backup.zip")
        tmp_p.replace(final_p)
        vault_path = final_p

        self.assertTrue(vault_path.is_file())
        original_size = vault_path.stat().st_size

        # Read Central Directory offset and tail bytes
        with zipfile.ZipFile(str(vault_path), "r") as zf:
            cd_offset = zf.start_dir
        with open(str(vault_path), "rb") as fp:
            fp.seek(cd_offset)
            tail_bytes = fp.read()

        # Write simulated journal file as if mid-append crash occurred
        journal_path = vault_path.parent / f"{vault_path.name}.vault_journal"
        with open(str(journal_path), "wb") as jfp:
            jfp.write(struct.pack("<8sQQ", VAULT_JOURNAL_MAGIC, original_size, cd_offset))
            jfp.write(tail_bytes)

        # Corrupt vault file by appending dirty incomplete data
        with open(str(vault_path), "ab") as fp:
            fp.write(b"CORRUPTED_INCOMPLETE_POWER_LOSS_DATA_999999999999999")

        self.assertGreater(vault_path.stat().st_size, original_size)

        # Call recover_vault_from_journal
        recovered = recover_vault_from_journal(vault_path)
        self.assertTrue(recovered)
        self.assertFalse(journal_path.exists())
        self.assertEqual(vault_path.stat().st_size, original_size)

        # Verify archive integrity and contents
        with zipfile.ZipFile(str(vault_path), "r") as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("Slot1.sav", zf.namelist())
            self.assertEqual(zf.read("Slot1.sav").decode("utf-8"), "INITIAL_VALID_SAVE_DATA_CONTENT")

    def test_vault_compaction_reclaims_space(self):
        """
        Test 2: Automated and manual vault compaction for dead space.
        Appends multiple versions of AutoSave.sav into the single master vault,
        verifies dead space calculation (>25%), runs compact_vault, and verifies
        size reduction and integrity.
        """
        vault_path = self.backups_dir / "Witcher_Backup.zip"
        save_file = self.saves_dir / "AutoSave.sav"

        # Create base archive with 10KB data
        save_file.write_bytes(b"A" * 10240)
        manifest = build_manifest(self.saves_dir)
        writer = ZipWriter()
        tmp_p, final_p = writer.create_snapshot_archive(self.saves_dir, self.backups_dir, manifest, "Witcher_Backup.zip")
        tmp_p.replace(final_p)
        vault_path = final_p

        # In-place append updated save versions 6 times
        for i in range(6):
            save_file.write_bytes(f"VERSION_{i}_".encode("utf-8") + b"B" * 10240)
            writer.append_new_files_to_archive(vault_path, self.saves_dir, ["AutoSave.sav"])

        # Check dead space calculation
        total_size, dead_bytes, dead_ratio = calculate_vault_dead_space(vault_path)
        self.assertGreater(dead_ratio, 0.25)

        # Perform compaction
        ok, old_size, new_size = compact_vault(vault_path)
        self.assertTrue(ok)
        self.assertLess(new_size, old_size)

        # Verify compacted archive
        with zipfile.ZipFile(str(vault_path), "r") as zf:
            self.assertIsNone(zf.testzip())
            namelist = zf.namelist()
            self.assertEqual(namelist.count("AutoSave.sav"), 1)
            # Latest content is preserved
            content = zf.read("AutoSave.sav")
            self.assertTrue(content.startswith(b"VERSION_5_"))

    def test_png_stored_without_recompression(self):
        """
        Test 3: Zero-compression bypass (ZIP_STORED) for pre-compressed images (.png).
        Verifies that .png files are stored with compression_type=ZIP_STORED (0)
        while .sav files are compressed with ZIP_DEFLATED (8).
        """
        sav_file = self.saves_dir / "ManualSave.sav"
        png_file = self.saves_dir / "ManualSave.png"

        sav_file.write_text("GAME_SAVE_UNCOMPRESSED_DATA" * 50, encoding="utf-8")
        png_file.write_bytes(VALID_1X1_PNG)

        self.assertTrue(should_store_without_compression("ManualSave.png"))
        self.assertFalse(should_store_without_compression("ManualSave.sav"))

        manifest = build_manifest(self.saves_dir)
        writer = ZipWriter(compression_level="deflated")
        tmp_p, final_p = writer.create_snapshot_archive(self.saves_dir, self.backups_dir, manifest, "MediaGame_Backup.zip")
        tmp_p.replace(final_p)
        vault_path = final_p

        with zipfile.ZipFile(str(vault_path), "r") as zf:
            for info in zf.infolist():
                if info.filename == "ManualSave.png":
                    self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                elif info.filename == "ManualSave.sav":
                    self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)

    def test_memory_trim_and_timer_pause_on_hide(self):
        """
        Test 4: PySide6 RAM trimming & timer suspension on hide/show.
        Verifies that hiding MainWindow pauses refresh timers and calling show() resumes them.
        """
        window = MainWindow(config_manager=self.config_mgr, db_manager=self.db)
        window.show()

        # Timers should be active when window is visible
        self.assertTrue(window.countdown_timer.isActive())
        self.assertTrue(window.page_dashboard.dashboard_timer.isActive())

        # Hide window: timers should be paused
        window.hide()
        self.assertFalse(window.countdown_timer.isActive())
        self.assertFalse(window.page_dashboard.dashboard_timer.isActive())

        # Show window again: timers should automatically resume
        window.show()
        self.assertTrue(window.countdown_timer.isActive())
        self.assertTrue(window.page_dashboard.dashboard_timer.isActive())

        window.close()

    def test_thumbnail_in_memory_preview(self):
        """
        Test 5: Visual save screenshot in-memory preview.
        Verifies that extract_screenshot_from_zip extracts image bytes without disk writes,
        and that RestoreDialog and HistoryPage load the thumbnail into a valid QPixmap.
        """
        sav_file = self.saves_dir / "QuickSave.sav"
        png_file = self.saves_dir / "QuickSave.png"
        sav_file.write_text("DATA", encoding="utf-8")
        png_file.write_bytes(VALID_1X1_PNG)

        manifest = build_manifest(self.saves_dir)
        writer = ZipWriter()
        tmp_p, final_p = writer.create_snapshot_archive(self.saves_dir, self.backups_dir, manifest, "PreviewGame_Backup.zip")
        tmp_p.replace(final_p)
        vault_path = final_p

        # 1. Direct zip extraction test
        extracted_bytes = extract_screenshot_from_zip(vault_path)
        self.assertIsNotNone(extracted_bytes)
        self.assertEqual(extracted_bytes, VALID_1X1_PNG)

        # 2. RestoreDialog test
        record = BackupRecord(
            id=1,
            game_name="PreviewGame",
            filename=vault_path.name,
            filepath=str(vault_path),
            size_bytes=vault_path.stat().st_size,
            file_count=2,
            sha256_hash="abc123",
            trigger_type="manual",
            status=BackupStatus.SUCCESS.value
        )
        self.db.add_backup(record)

        from src.restore.restore_manager import RestoreManager
        restore_mgr = RestoreManager(self.db)

        dialog = RestoreDialog(record, restore_mgr, str(self.saves_dir), str(self.backups_dir))
        self.assertIsNotNone(dialog.preview_pixmap)
        self.assertFalse(dialog.preview_pixmap.isNull())
        self.assertIsNotNone(dialog.lbl_preview.pixmap())
        self.assertFalse(dialog.lbl_preview.pixmap().isNull())
        dialog.close()

        # 3. HistoryPage test
        history = HistoryPage(self.db, restore_mgr)
        history.refresh_records()
        self.assertEqual(history.table.rowCount(), 1)
        history.table.selectRow(0)
        self.assertIsNotNone(history.lbl_preview.pixmap())
        self.assertFalse(history.lbl_preview.pixmap().isNull())
        history.close()

    def test_silent_gaming_mode_suppresses_auto_notifications(self):
        """
        Test 6: Silent Gaming Mode notification suppression.
        Verifies that automated routine notifications are suppressed when
        silent_gaming_mode=True, while manual backups and critical errors are delivered.
        """
        self.settings.silent_gaming_mode = True
        self.settings.notifications_enabled = True
        window = MainWindow(config_manager=self.config_mgr, db_manager=self.db)

        # Automated routine backup: suppressed
        auto_res = window.notify("Routine Backup", "Snapshot created", is_critical=False, is_manual=False)
        self.assertFalse(auto_res)

        # Manual user-triggered backup: delivered
        manual_res = window.notify("Manual Backup", "Backup completed", is_critical=False, is_manual=True)
        self.assertTrue(manual_res)

        # Critical error alert: delivered
        err_res = window.notify("Error Alert", "Disk full error", is_critical=True, is_manual=False)
        self.assertTrue(err_res)

        window.close()

    def test_cli_minimized_startup_flag(self):
        """
        Test 7: Silent background startup via --minimized CLI flag.
        Verifies that create_app_window(minimized=True) constructs the window in hidden state.
        """
        app, window = create_app_window(
            minimized=True,
            config_mgr=self.config_mgr,
            db_mgr=self.db
        )
        self.assertFalse(window.isVisible())
        window.close()

    def test_multi_game_profiles_and_auto_detector_speed(self):
        """
        Test 8: Universal multi-game profile auto-detector.
        Verifies that auto_detect_all_games discovers games across mock directory trees,
        ignores blacklisted ShaderCache directories, and executes in < 50ms.
        """
        mock_root = self.base / "MockUserGames"
        mock_root.mkdir(parents=True, exist_ok=True)

        # Game 1: Elden Ring with .sl2 save
        er_dir = mock_root / "Elden Ring" / "saves"
        er_dir.mkdir(parents=True, exist_ok=True)
        (er_dir / "ER0000.sl2").write_bytes(b"ELDEN_SAVE")

        # Game 2: Skyrim with .ess save
        skyrim_dir = mock_root / "Skyrim" / "savegames"
        skyrim_dir.mkdir(parents=True, exist_ok=True)
        (skyrim_dir / "quicksave.ess").write_bytes(b"SKYRIM_SAVE")

        # Game 3: Witcher 3 with .sav save
        witcher_dir = mock_root / "The Witcher 3" / "gamesaves"
        witcher_dir.mkdir(parents=True, exist_ok=True)
        (witcher_dir / "AutoSave.sav").write_bytes(b"WITCHER_SAVE")

        # Blacklisted: ShaderCache folder should be ignored
        shader_dir = mock_root / "UnrealGame" / "ShaderCache"
        shader_dir.mkdir(parents=True, exist_ok=True)
        (shader_dir / "cached_shaders.dat").write_bytes(b"SHADER_CACHE_DATA")

        # Execute auto-detector and measure duration
        start_time = time.perf_counter()
        detected = auto_detect_all_games(custom_roots=[mock_root])
        elapsed = time.perf_counter() - start_time

        # Assert performance benchmark: < 50ms (< 0.05s)
        self.assertLess(elapsed, 0.05, f"Detection took too long: {elapsed:.4f}s")

        # Assert all 3 games detected
        self.assertIn("Elden Ring", detected)
        self.assertIn("Skyrim", detected)
        w3_title = "The Witcher 3: Wild Hunt" if "The Witcher 3: Wild Hunt" in detected else "The Witcher 3"
        self.assertIn(w3_title, detected)

        # Assert blacklisted folder ignored
        self.assertNotIn("UnrealGame", detected)
        self.assertNotIn("ShaderCache", detected)

        # Verify detected paths
        self.assertEqual(detected["Elden Ring"], er_dir)
        self.assertEqual(detected["Skyrim"], skyrim_dir)
        self.assertEqual(detected[w3_title], witcher_dir)


if __name__ == "__main__":
    unittest.main()
