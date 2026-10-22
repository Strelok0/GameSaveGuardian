"""
Tests for SQLite crash recovery, persistence across restarts,
and unavailable directory handling.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from src.database import DatabaseManager
from src.models import BackupRecord, BackupStatus, VerificationStatus


class TestDatabaseAndCrashRecovery(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_db_")
        self.base = Path(self.temp_dir)
        self.db_path = self.base / "guardian.db"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_database_persistence_after_restart(self):
        """Test 20: Database persists records when re-opened across application restarts."""
        # 1. First run: add records
        db1 = DatabaseManager(self.db_path)
        rec = BackupRecord(
            game_name="Witcher 3",
            filename="Witcher3_2026-10-21.zip",
            filepath=str(self.base / "Witcher3_2026-10-21.zip"),
            timestamp="2026-10-21T10:00:00",
            size_bytes=1024,
            file_count=5,
            status=BackupStatus.SUCCESS.value,
            verification_status=VerificationStatus.VERIFIED_OK.value
        )
        rec_id = db1.add_backup(rec)
        self.assertGreater(rec_id, 0)
        del db1  # Simulate app close

        # 2. Re-open database (simulate app restart)
        db2 = DatabaseManager(self.db_path)
        fetched = db2.get_backup_by_id(rec_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.filename, "Witcher3_2026-10-21.zip")
        self.assertEqual(fetched.status, BackupStatus.SUCCESS.value)

    def test_recover_from_interrupted_backup_operation(self):
        """Test 16: Reconciliation marks interrupted or abandoned backups after a crash."""
        db = DatabaseManager(self.db_path)

        # Simulate an interrupted backup that was marked IN_PROGRESS when app crashed
        interrupted_rec = BackupRecord(
            game_name="Witcher 3",
            filename="Incomplete_2026.zip",
            filepath=str(self.base / "Incomplete_2026.zip"),
            timestamp="2026-10-21T11:00:00",
            size_bytes=0,
            file_count=0,
            status=BackupStatus.IN_PROGRESS.value,
            verification_status=VerificationStatus.UNVERIFIED.value
        )
        i_id = db.add_backup(interrupted_rec)

        # Run startup reconciliation
        missing, fixed_interrupted = db.reconcile_backups("Witcher 3")
        self.assertEqual(fixed_interrupted, 1)

        # Verify status is now INTERRUPTED with error explanation
        updated = db.get_backup_by_id(i_id)
        self.assertEqual(updated.status, BackupStatus.INTERRUPTED.value)
        self.assertIn("crashed", updated.error_message.lower())

    def test_reconciliation_detects_physically_deleted_archives(self):
        """Startup reconciliation marks backups whose files disappeared from disk."""
        db = DatabaseManager(self.db_path)

        rec = BackupRecord(
            game_name="Witcher 3",
            filename="GhostArchive.zip",
            filepath=str(self.base / "GhostArchive.zip"),  # Does not exist on disk
            timestamp="2026-10-21T11:30:00",
            size_bytes=5000,
            file_count=2,
            status=BackupStatus.SUCCESS.value,
            verification_status=VerificationStatus.VERIFIED_OK.value
        )
        rec_id = db.add_backup(rec)

        missing_count, _ = db.reconcile_backups("Witcher 3")
        self.assertEqual(missing_count, 1)

        updated = db.get_backup_by_id(rec_id)
        self.assertEqual(updated.verification_status, VerificationStatus.VERIFIED_FAIL.value)
        self.assertIn("missing", updated.error_message.lower())

    def test_get_total_storage_used_deduplicates_same_archive(self):
        """Storage used calculates physical space of unique files rather than summing duplicates."""
        db = DatabaseManager(self.db_path)
        vault_path = self.base / "The_Witcher_3_Backup.zip"
        vault_path.write_bytes(b"X" * 1500)  # Real 1500 bytes on disk

        # Simulate 5 backups updating the same archive (Single Vault mode)
        for i in range(5):
            rec = BackupRecord(
                game_name="Witcher 3",
                filename=vault_path.name,
                filepath=str(vault_path),
                timestamp=f"2026-10-21T12:0{i}:00",
                size_bytes=1500,
                file_count=10 + i,
                status=BackupStatus.SUCCESS.value,
                verification_status=VerificationStatus.VERIFIED_OK.value
            )
            db.add_backup(rec)

        # Total storage must be 1500 bytes, NOT 5 * 1500 = 7500 bytes!
        total = db.get_total_storage_used("Witcher 3")
        self.assertEqual(total, 1500)


if __name__ == "__main__":
    unittest.main()
