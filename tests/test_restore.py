"""
Tests for safe restore operations, staging extraction, pre-restore safety backups,
and existing file protection.
"""

from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from src.database import DatabaseManager
from src.restore.restore_manager import RestoreManager


class TestRestoreSystem(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_restore_")
        self.base = Path(self.temp_dir)
        self.archive_dir = self.base / "archives"
        self.active_save_dir = self.base / "active_saves"
        self.separate_staging_dir = self.base / "inspected_restore"
        self.db_path = self.base / "test.db"

        self.archive_dir.mkdir()
        self.active_save_dir.mkdir()
        self.separate_staging_dir.mkdir()

        self.db = DatabaseManager(self.db_path)
        self.restore_mgr = RestoreManager(self.db)

        # Create a sample archive to restore
        self.zip_path = self.archive_dir / "Backup_2026.zip"
        with zipfile.ZipFile(str(self.zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ManualSave_001.sav", b"RESTORED_SAVE_VERSION_1")
            zf.writestr("subfolder/nested.sav", b"RESTORED_NESTED_VERSION_1")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_restore_to_separate_directory(self):
        """Test 12: Safe restoration into a separate inspection folder."""
        ok, msg, safety_path = self.restore_mgr.execute_restore(
            zip_path=self.zip_path,
            target_dir=self.separate_staging_dir,
            is_direct_restore=False
        )
        self.assertTrue(ok, f"Restore failed: {msg}")
        self.assertIsNone(safety_path)  # Separate restore doesn't overwrite active saves

        # Check extracted files
        f1 = self.separate_staging_dir / "ManualSave_001.sav"
        f2 = self.separate_staging_dir / "subfolder" / "nested.sav"
        self.assertTrue(f1.is_file())
        self.assertEqual(f1.read_bytes(), b"RESTORED_SAVE_VERSION_1")
        self.assertTrue(f2.is_file())
        self.assertEqual(f2.read_bytes(), b"RESTORED_NESTED_VERSION_1")

    def test_safety_backup_before_overwriting_files(self):
        """Test 14: Direct restore creates an emergency safety backup of current files."""
        # Active saves contains existing progress
        existing_file = self.active_save_dir / "ManualSave_001.sav"
        existing_file.write_bytes(b"CURRENT_ACTIVE_GAMEPLAY_PROGRESS")

        ok, msg, safety_path = self.restore_mgr.execute_restore(
            zip_path=self.zip_path,
            target_dir=self.active_save_dir,
            is_direct_restore=True,
            backup_dir_for_safety=self.archive_dir
        )
        self.assertTrue(ok, f"Restore failed: {msg}")
        self.assertIsNotNone(safety_path)
        self.assertTrue(safety_path.is_file())

        # Verify safety backup contains the pre-restore content
        with zipfile.ZipFile(str(safety_path), "r") as zf:
            self.assertIn("ManualSave_001.sav", zf.namelist())
            safety_content = zf.read("ManualSave_001.sav")
            self.assertEqual(safety_content, b"CURRENT_ACTIVE_GAMEPLAY_PROGRESS")

        # Verify active file has now been updated to restored version
        self.assertEqual(existing_file.read_bytes(), b"RESTORED_SAVE_VERSION_1")

    def test_protect_untouched_files_during_restore(self):
        """Test 13: Files not present in archive are not deleted during restore."""
        untouched_file = self.active_save_dir / "UnrelatedSave_999.sav"
        untouched_file.write_bytes(b"DO_NOT_DELETE_ME")

        ok, msg, _ = self.restore_mgr.execute_restore(
            zip_path=self.zip_path,
            target_dir=self.active_save_dir,
            is_direct_restore=True,
            backup_dir_for_safety=self.archive_dir
        )
        self.assertTrue(ok)
        self.assertTrue(untouched_file.is_file())
        self.assertEqual(untouched_file.read_bytes(), b"DO_NOT_DELETE_ME")

    def test_restore_with_progress_callback(self):
        """Test progress callback during execution."""
        progress_events = []

        def _cb(cur, tot, txt):
            progress_events.append((cur, tot, txt))

        ok, msg, _ = self.restore_mgr.execute_restore(
            zip_path=self.zip_path,
            target_dir=self.separate_staging_dir,
            is_direct_restore=False,
            progress_callback=_cb
        )
        self.assertTrue(ok)
        self.assertGreater(len(progress_events), 0)
        # Final progress event should reach 100%
        self.assertEqual(progress_events[-1][0], 100)

    def test_generate_preview_with_cached_list(self):
        """Test generating preview using cached archive list."""
        cached_items = [
            {"filename": "ManualSave_001.sav", "size": 100},
            {"filename": "NewSave_999.sav", "size": 200}
        ]
        # Active saves has ManualSave_001.sav
        (self.active_save_dir / "ManualSave_001.sav").write_bytes(b"existing")

        ok, preview, msg = self.restore_mgr.generate_preview(
            zip_path=self.zip_path,
            target_dir=self.active_save_dir,
            cached_file_list=cached_items
        )
        self.assertTrue(ok)
        self.assertIn("ManualSave_001.sav", preview.files_to_replace)
        self.assertIn("NewSave_999.sav", preview.files_to_add)

    def test_auto_heal_restores_only_missing_files_without_overwriting(self):
        """Test auto-heal restores only missing save files and preserves existing ones untouched."""
        # 1. Existing save in active directory
        existing_file = self.active_save_dir / "ManualSave_001.sav"
        existing_file.write_bytes(b"LATEST_LOCAL_PROGRESS_DO_NOT_TOUCH")

        # 2. Archive has ManualSave_001.sav (old version) + subfolder/nested.sav (missing)
        result = self.restore_mgr.check_and_restore_missing_saves(
            save_dir=self.active_save_dir,
            backup_path_or_dir=self.archive_dir,
            game_name="TestGame",
            dry_run=False
        )

        self.assertTrue(result.checked)
        self.assertEqual(result.missing_found, 1)
        self.assertEqual(len(result.restored_files), 1)
        self.assertIn("subfolder/nested.sav", [f.replace("\\", "/") for f in result.restored_files])

        # Verify existing file was NOT overwritten
        self.assertEqual(existing_file.read_bytes(), b"LATEST_LOCAL_PROGRESS_DO_NOT_TOUCH")

        # Verify missing file WAS restored
        restored_nested = self.active_save_dir / "subfolder" / "nested.sav"
        self.assertTrue(restored_nested.is_file())
        self.assertEqual(restored_nested.read_bytes(), b"RESTORED_NESTED_VERSION_1")

    def test_auto_heal_when_all_files_present(self):
        """Test auto-heal when all files in archive are already present in active directory."""
        (self.active_save_dir / "ManualSave_001.sav").write_bytes(b"EXISTS_1")
        (self.active_save_dir / "subfolder").mkdir(parents=True, exist_ok=True)
        (self.active_save_dir / "subfolder" / "nested.sav").write_bytes(b"EXISTS_2")

        result = self.restore_mgr.check_and_restore_missing_saves(
            save_dir=self.active_save_dir,
            backup_path_or_dir=self.archive_dir,
            game_name="TestGame",
            dry_run=False
        )

        self.assertTrue(result.checked)
        self.assertEqual(result.missing_found, 0)
        self.assertEqual(len(result.restored_files), 0)
        self.assertIn("All save files are verified", result.message)

    def test_auto_heal_dry_run(self):
        """Test auto-heal dry run identifies missing files without writing to disk."""
        # Active saves is empty, archive has 2 files
        result = self.restore_mgr.check_and_restore_missing_saves(
            save_dir=self.active_save_dir,
            backup_path_or_dir=self.archive_dir,
            game_name="TestGame",
            dry_run=True
        )

        self.assertTrue(result.checked)
        self.assertEqual(result.missing_found, 2)
        # Should not have created files on disk
        self.assertFalse((self.active_save_dir / "ManualSave_001.sav").exists())

    def test_auto_heal_no_archives(self):
        """Test auto-heal behavior when no backup archives exist."""
        empty_dir = self.base / "empty_backups"
        empty_dir.mkdir()

        result = self.restore_mgr.check_and_restore_missing_saves(
            save_dir=self.active_save_dir,
            backup_path_or_dir=empty_dir,
            game_name="TestGame"
        )
        self.assertTrue(result.checked)
        self.assertEqual(result.missing_found, 0)
        self.assertIn("No backup archives found", result.message)


if __name__ == "__main__":
    unittest.main()
