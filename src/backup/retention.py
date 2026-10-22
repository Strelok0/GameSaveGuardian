"""
Backup retention policy enforcement and storage pruning.
Enforces safety rules: never deletes the latest valid backup or active backups.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
from typing import List, Optional, Set

from src.models import BackupRecord, BackupStatus, RetentionPolicy, VerificationStatus
from src.utils.logging_config import get_logger

logger = get_logger()


class RetentionManager:
    """Evaluates and applies backup pruning rules safely."""

    def __init__(self, policy: RetentionPolicy):
        self.policy = policy

    def evaluate_candidates(
        self,
        records: List[BackupRecord],
        protected_ids: Optional[Set[int]] = None
    ) -> List[BackupRecord]:
        """
        Determine which backup records are eligible for deletion according to the retention policy.
        Returns a list of records to be pruned.
        Does NOT perform file deletions.
        """
        if not self.policy.enabled or not records:
            return []

        protected: Set[int] = set(protected_ids or [])

        # Filter only successful, verified backups that exist
        valid_records = [
            r for r in records
            if r.status == BackupStatus.SUCCESS.value
            and r.verification_status == VerificationStatus.VERIFIED_OK.value
        ]

        if not valid_records:
            return []

        # Sort descending by ID (newest first)
        valid_records.sort(key=lambda r: (r.id or 0), reverse=True)

        # Rule 1: NEVER delete the latest valid backup
        latest_id = valid_records[0].id
        if latest_id:
            protected.add(latest_id)

        now = datetime.now()
        candidates_to_prune: List[BackupRecord] = []

        # Group backups by date for daily snapshot retention
        daily_snapshots_preserved: Set[str] = set()

        for idx, rec in enumerate(valid_records):
            if rec.id in protected:
                continue

            try:
                rec_dt = datetime.fromisoformat(rec.timestamp)
            except Exception:
                # If timestamp cannot be parsed, protect it rather than deleting
                protected.add(rec.id or 0)
                continue

            age_days = (now - rec_dt).total_seconds() / 86400.0
            date_key = rec_dt.strftime("%Y-%m-%d")

            # 1. Inside keep_all_days window: keep everything
            if age_days <= self.policy.keep_all_days:
                continue

            # 2. Inside daily_snapshot_days window: keep one snapshot per day
            if age_days <= self.policy.daily_snapshot_days:
                if date_key not in daily_snapshots_preserved:
                    daily_snapshots_preserved.add(date_key)
                    continue

            # 3. Inside max_count window: keep up to max_count recent backups
            if idx < self.policy.max_count:
                continue

            # If not preserved by any rule, it's a candidate for pruning
            candidates_to_prune.append(rec)

        return candidates_to_prune

    def apply_retention(
        self,
        records: List[BackupRecord],
        db_manager,
        protected_ids: Optional[Set[int]] = None
    ) -> List[BackupRecord]:
        """
        Prunes files for evaluated candidates and updates database records.
        Returns the list of successfully deleted records.
        """
        candidates = self.evaluate_candidates(records, protected_ids=protected_ids)
        deleted: List[BackupRecord] = []

        for rec in candidates:
            path = Path(rec.filepath)
            try:
                if path.is_file():
                    path.unlink()
                    logger.info("Retention: safely deleted pruned archive %s", path)

                # Delete from database or update status
                if rec.id:
                    db_manager.delete_backup(rec.id)
                    db_manager.add_audit_log(
                        "RETENTION_PRUNED",
                        f"Pruned backup #{rec.id} ({rec.filename}) based on retention policy."
                    )

                deleted.append(rec)
            except Exception as e:
                logger.error("Failed to prune backup archive %s: %s", path, e)

        return deleted


def check_and_compact_vault_if_needed(vault_path: Path | str, threshold_ratio: float = 0.25) -> tuple[bool, int, int]:
    """
    Check if the vault has dead space exceeding threshold_ratio (default 25%).
    If so, runs compact_vault().
    Returns: (compacted: bool, old_size: int, new_size: int)
    """
    from src.backup.zip_writer import calculate_vault_dead_space, compact_vault
    total_size, dead_bytes, ratio = calculate_vault_dead_space(vault_path)
    if ratio >= threshold_ratio and total_size > 0:
        logger.info(
            "Vault %s dead space ratio is %.1f%% (threshold %.1f%%). Triggering automated compaction.",
            Path(vault_path).name, ratio * 100, threshold_ratio * 100
        )
        return compact_vault(vault_path)
    return False, total_size, total_size
