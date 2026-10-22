"""
Tests for retention pruning policies and safety rules (e.g. never deleting latest backup).
"""

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import tempfile
import unittest

from src.backup.retention import RetentionManager
from src.database import DatabaseManager
from src.models import BackupRecord, BackupStatus, RetentionPolicy, VerificationStatus


class TestRetentionPolicy(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_retention_")
        self.base = Path(self.temp_dir)
        self.db = DatabaseManager(self.base / "test.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_never_delete_latest_valid_backup(self):
        """Test 15: Retention policy must NEVER delete the latest valid backup."""
        policy = RetentionPolicy(
            enabled=True,
            max_count=1,  # Strict: keep only 1
            keep_all_days=0,
            daily_snapshot_days=0
        )
        ret_mgr = RetentionManager(policy)

        # Create 3 dummy backup records
        records = []
        now = datetime.now()
        for i in range(1, 4):
            fpath = self.base / f"backup_{i}.zip"
            fpath.write_bytes(b"DUMMY_ZIP")
            rec = BackupRecord(
                id=i,
                game_name="Witcher 3",
                filename=f"backup_{i}.zip",
                filepath=str(fpath),
                timestamp=(now - timedelta(days=10 - i)).isoformat(),
                size_bytes=100,
                file_count=1,
                status=BackupStatus.SUCCESS.value,
                verification_status=VerificationStatus.VERIFIED_OK.value
            )
            records.append(rec)

        candidates = ret_mgr.evaluate_candidates(records)
        pruned_ids = [c.id for c in candidates]

        # The latest record (id=3) must NOT be in candidates!
        self.assertNotIn(3, pruned_ids, "Latest backup #3 was marked for pruning!")
        self.assertIn(1, pruned_ids)
        self.assertIn(2, pruned_ids)

    def test_daily_snapshot_preservation(self):
        """Verify that within daily_snapshot_days window, one backup per day is preserved."""
        policy = RetentionPolicy(
            enabled=True,
            max_count=2,
            keep_all_days=1,
            daily_snapshot_days=10
        )
        ret_mgr = RetentionManager(policy)

        records = []
        now = datetime.now()
        # Create 3 backups on day -5
        day_5_dt = now - timedelta(days=5)
        for i in range(1, 4):
            fpath = self.base / f"b_day5_{i}.zip"
            fpath.write_bytes(b"DUMMY")
            records.append(BackupRecord(
                id=i,
                game_name="Witcher 3",
                filename=f"b_day5_{i}.zip",
                filepath=str(fpath),
                timestamp=(day_5_dt + timedelta(hours=i)).isoformat(),
                size_bytes=100,
                file_count=1,
                status=BackupStatus.SUCCESS.value,
                verification_status=VerificationStatus.VERIFIED_OK.value
            ))

        # Add newest backup today (id=4)
        fpath_today = self.base / "b_today.zip"
        fpath_today.write_bytes(b"DUMMY")
        records.append(BackupRecord(
            id=4,
            game_name="Witcher 3",
            filename="b_today.zip",
            filepath=str(fpath_today),
            timestamp=now.isoformat(),
            size_bytes=100,
            file_count=1,
            status=BackupStatus.SUCCESS.value,
            verification_status=VerificationStatus.VERIFIED_OK.value
        ))

        candidates = ret_mgr.evaluate_candidates(records)
        pruned_ids = [c.id for c in candidates]

        # Exactly 2 of the 3 backups from day -5 should be pruned, preserving 1 daily snapshot
        day_5_pruned = [cid for cid in pruned_ids if cid in (1, 2, 3)]
        self.assertEqual(len(day_5_pruned), 2)
        self.assertNotIn(4, pruned_ids)


if __name__ == "__main__":
    unittest.main()
