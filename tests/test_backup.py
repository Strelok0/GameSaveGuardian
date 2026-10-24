"""
Tests for backup creation, relative paths, Unicode filenames,
change detection, and recursive directory prevention.
"""

import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
import zipfile

from src.backup.backup_manager import BackupManager
from src.backup.integrity import build_manifest
from src.database import DatabaseManager
from src.models import (
    AppSettings,
    BackupStatus,
    BackupTrigger,
    GameConfig,
    VerificationStatus,
)
from src.utils.paths import is_path_overlapping_or_recursive


class TestBackupEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_backup_")
        self.base = Path(self.temp_dir)
        self.save_dir = self.base / "gamesaves"
        self.backup_dir = self.base / "backups"
        self.db_path = self.base / "test.db"

        self.save_dir.mkdir(parents=True)
        self.backup_dir.mkdir(parents=True)

        self.db = DatabaseManager(self.db_path)
        self.settings = AppSettings(
            active_game=GameConfig(
                name="TestGame",
                save_directory=str(self.save_dir),
                backup_directory=str(self.backup_dir)
            )
        )
        self.mgr = BackupManager(self.db, self.settings)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_zip_backup_and_preserve_relative_paths(self):
        """Test 1 & 2: Creating a ZIP backup and preserving relative folder paths."""
        # Create nested file structure
        sub = self.save_dir / "slot1"
        sub.mkdir()
        (self.save_dir / "root_save.sav").write_bytes(b"ROOT_SAVE_CONTENT")
        (sub / "nested_save.sav").write_bytes(b"NESTED_SAVE_CONTENT")

        ok, msg, record = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok, f"Backup failed: {msg}")
        self.assertIsNotNone(record)
        self.assertTrue(Path(record.filepath).is_file())

        # Inspect ZIP archive contents
        with zipfile.ZipFile(record.filepath, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("root_save.sav", namelist)
            self.assertIn("slot1/nested_save.sav", namelist)

    def test_unicode_filenames(self):
        """Test 3: Handling Unicode and non-ASCII save filenames."""
        unicode_name = "حفظ_اللعبة_🧙‍♂️_Witcher_⚔️.sav"
        (self.save_dir / unicode_name).write_text("Unicode Witcher Save Content", encoding="utf-8")

        ok, msg, record = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok)
        self.assertIsNotNone(record)

        with zipfile.ZipFile(record.filepath, "r") as zf:
            namelist = zf.namelist()
            self.assertIn(unicode_name, namelist)

    def test_detect_new_files(self):
        """Test 4: Manifest detects newly added files."""
        (self.save_dir / "save1.sav").write_text("Save 1")
        m1 = build_manifest(self.save_dir)

        # Add new file
        (self.save_dir / "save2.sav").write_text("Save 2")
        m2 = build_manifest(self.save_dir)

        self.assertTrue(m2.has_changed_from(m1))
        self.assertEqual(m2.file_count, 2)

    def test_detect_modified_files(self):
        """Test 5: Manifest detects modified files."""
        f = self.save_dir / "save1.sav"
        f.write_text("Version 1")
        m1 = build_manifest(self.save_dir)

        time.sleep(0.05)
        f.write_text("Version 2 - Updated Progress")
        m2 = build_manifest(self.save_dir)

        self.assertTrue(m2.has_changed_from(m1))

    def test_detect_deleted_files(self):
        """Test 6: Manifest detects deleted files."""
        f1 = self.save_dir / "save1.sav"
        f2 = self.save_dir / "save2.sav"
        f1.write_text("Save 1")
        f2.write_text("Save 2")
        m1 = build_manifest(self.save_dir)

        f2.unlink()
        m2 = build_manifest(self.save_dir)

        self.assertTrue(m2.has_changed_from(m1))
        self.assertEqual(m2.file_count, 1)

    def test_skip_backup_when_no_changes(self):
        """Test 7: Skipping backup when no files changed."""
        (self.save_dir / "save.sav").write_text("Save Data")

        # First backup
        ok1, _, rec1 = self.mgr.execute_backup(trigger=BackupTrigger.INITIAL, force=False)
        self.assertTrue(ok1)
        self.assertIsNotNone(rec1)

        # Second backup without modifications
        ok2, msg2, rec2 = self.mgr.execute_backup(trigger=BackupTrigger.SCHEDULED, force=False)
        self.assertTrue(ok2)
        self.assertIsNone(rec2, "Redundant backup was not skipped!")
        self.assertIn("No changes detected", msg2)

    def test_prevent_source_destination_recursion(self):
        """Test 18: Rejecting recursive and overlapping paths."""
        # Destination inside source
        dest_inside = self.save_dir / "backups_inside"
        overlap1, _ = is_path_overlapping_or_recursive(self.save_dir, dest_inside)
        self.assertTrue(overlap1)

        # Source inside destination
        src_inside = self.backup_dir / "saves_inside"
        overlap2, _ = is_path_overlapping_or_recursive(src_inside, self.backup_dir)
        self.assertTrue(overlap2)

        # Identical
        overlap3, _ = is_path_overlapping_or_recursive(self.save_dir, self.save_dir)
        self.assertTrue(overlap3)

    def test_single_vault_appends_only_new_files(self):
        """Single Vault mode appends newly added files into the same master archive without creating multiple ZIPs."""
        self.settings.archive_mode = "single_vault"
        (self.save_dir / "save1.sav").write_text("Save 1 Content")
        (self.save_dir / "save2.sav").write_text("Save 2 Content")

        # Initial backup creates the single vault
        ok1, msg1, rec1 = self.mgr.execute_backup(trigger=BackupTrigger.INITIAL, force=True)
        self.assertTrue(ok1)
        self.assertEqual(rec1.filename, "TestGame_Backup.zip")

        zips = list(self.backup_dir.glob("*.zip"))
        self.assertEqual(len(zips), 1)

        # Now add a NEW save file
        (self.save_dir / "save3_new.sav").write_text("Save 3 New Content")

        # Second backup should append to the same ZIP
        ok2, msg2, rec2 = self.mgr.execute_backup(trigger=BackupTrigger.REALTIME, force=False)
        self.assertTrue(ok2)
        self.assertEqual(rec2.filename, "TestGame_Backup.zip")
        self.assertIn("+1 new files", rec2.error_message)

        # Still only 1 zip in the backup directory!
        zips_after = list(self.backup_dir.glob("*.zip"))
        self.assertEqual(len(zips_after), 1, "A second zip archive was created instead of appending!")

        # Verify that the single archive contains all 3 files
        with zipfile.ZipFile(self.backup_dir / "TestGame_Backup.zip", "r") as zf:
            names = zf.namelist()
            self.assertIn("save1.sav", names)
            self.assertIn("save2.sav", names)
            self.assertIn("save3_new.sav", names)
            self.assertIsNone(zf.testzip())

    def test_single_vault_adopts_existing_timestamped_backup(self):
        """Single Vault mode automatically adopts an existing timestamped backup if present."""
        self.settings.archive_mode = "single_vault"
        # Simulate user already having a timestamped backup from before
        ts_zip = self.backup_dir / "TestGame_2026-10-21_12-00-00.zip"
        with zipfile.ZipFile(ts_zip, "w") as zf:
            zf.writestr("old_save.sav", b"OLD_CONTENT")

        (self.save_dir / "old_save.sav").write_bytes(b"OLD_CONTENT")
        (self.save_dir / "newly_added.sav").write_bytes(b"NEWLY_ADDED_CONTENT")

        ok, msg, rec = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok)
        master_vault = self.backup_dir / "TestGame_Backup.zip"
        self.assertTrue(master_vault.is_file())

        with zipfile.ZipFile(master_vault, "r") as zf:
            self.assertIn("old_save.sav", zf.namelist())
            self.assertIn("newly_added.sav", zf.namelist())

    def test_single_vault_forced_manual_backup_when_no_changes(self):
        """Single Vault mode handles manual 'Backup Now' when 0 files changed without rebuilding archive."""
        self.settings.archive_mode = "single_vault"
        (self.save_dir / "save1.sav").write_text("Save 1")

        # Initial backup
        ok1, _, rec1 = self.mgr.execute_backup(trigger=BackupTrigger.INITIAL, force=True)
        self.assertTrue(ok1)
        self.assertIsNotNone(rec1)

        # Forced manual backup when no files changed
        ok2, msg2, rec2 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok2)
        self.assertIsNotNone(rec2)
        self.assertIn("up to date", msg2.lower())
        self.assertEqual(rec2.error_message, "Up to date (0 new files)")
        self.assertLess(rec2.duration_seconds, 1.0)

    def test_preserve_existing_valid_backups_after_failed_backup(self):
        """Test 19: Preserving existing valid backups when a subsequent backup fails."""
        (self.save_dir / "good.sav").write_text("Good Save Data")
        ok1, _, rec1 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok1)
        self.assertTrue(Path(rec1.filepath).exists())

        # Now simulate a failure by pointing save_directory to a non-existent path
        self.settings.active_game.save_directory = str(self.base / "non_existent_folder")
        ok2, msg2, rec2 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertFalse(ok2)
        self.assertIsNone(rec2)

        # Verify initial backup is intact
        self.assertTrue(Path(rec1.filepath).is_file())

    def test_incremental_snapshots_mode_only_saves_new_files(self):
        """Separate Snapshots mode only saves newly added or modified files, not a full re-copy."""
        self.settings.archive_mode = "snapshots"

        # 1. Baseline: 2 save files
        (self.save_dir / "save1.sav").write_bytes(b"SAVE_FILE_1_BASE")
        (self.save_dir / "save2.sav").write_bytes(b"SAVE_FILE_2_BASE")

        ok1, msg1, rec1 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=True)
        self.assertTrue(ok1)
        self.assertEqual(rec1.file_count, 2)
        with zipfile.ZipFile(rec1.filepath, "r") as zf1:
            self.assertIn("save1.sav", zf1.namelist())
            self.assertIn("save2.sav", zf1.namelist())

        # 2. Add 1 new file only
        time.sleep(1.0)
        (self.save_dir / "save3_new.sav").write_bytes(b"SAVE_FILE_3_NEW")

        ok2, msg2, rec2 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=False)
        self.assertTrue(ok2)
        self.assertIsNotNone(rec2)
        self.assertEqual(rec2.file_count, 1)
        self.assertIn("+1 new files", rec2.error_message)

        # Inspect the second snapshot ZIP: it MUST contain ONLY save3_new.sav!
        with zipfile.ZipFile(rec2.filepath, "r") as zf2:
            names2 = [n for n in zf2.namelist() if n != "_manifest.json"]
            self.assertEqual(names2, ["save3_new.sav"])
            self.assertNotIn("save1.sav", zf2.namelist())
            self.assertNotIn("save2.sav", zf2.namelist())

        # 3. Third run without changes: redundant backup skipped
        ok3, msg3, rec3 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=False)
        self.assertTrue(ok3)
        self.assertIsNone(rec3)
        self.assertIn("No changes detected", msg3)

        # 4. Modify an existing file (save1.sav)
        time.sleep(1.1)
        (self.save_dir / "save1.sav").write_bytes(b"SAVE_FILE_1_MODIFIED_LONGER_CONTENT")
        ok4, msg4, rec4 = self.mgr.execute_backup(trigger=BackupTrigger.MANUAL, force=False)
        self.assertTrue(ok4)
        self.assertIsNotNone(rec4)
        self.assertEqual(rec4.file_count, 1)
        with zipfile.ZipFile(rec4.filepath, "r") as zf4:
            names4 = [n for n in zf4.namelist() if n != "_manifest.json"]
            self.assertEqual(names4, ["save1.sav"])
            self.assertNotIn("save2.sav", zf4.namelist())
            self.assertNotIn("save3_new.sav", zf4.namelist())


if __name__ == "__main__":
    unittest.main()
