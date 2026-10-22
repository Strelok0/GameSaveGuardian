"""
SQLite persistence layer for Game Save Guardian.
Uses Write-Ahead Logging (WAL) and synchronous durability settings.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Generator, List, Optional, Tuple

from src.models import BackupRecord, BackupStatus, VerificationStatus
from src.utils.logging_config import get_logger
from src.utils.paths import get_database_path


class DatabaseManager:
    """Manages SQLite storage for backup history, manifests, and audit logs."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or get_database_path()
        self.logger = get_logger()
        self._init_db()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Create a connection configured for crash resilience and WAL mode, safely closing it on exit."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            # Durability and concurrency pragmas
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA foreign_keys = ON;")
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database tables and indexes."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_name TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    file_count INTEGER NOT NULL,
                    trigger_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    sha256_hash TEXT NOT NULL,
                    error_message TEXT,
                    manifest_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_backups_game_name ON backups(game_name);
                CREATE INDEX IF NOT EXISTS idx_backups_status ON backups(status);
                CREATE INDEX IF NOT EXISTS idx_backups_timestamp ON backups(timestamp DESC);

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def add_backup(self, record: BackupRecord) -> int:
        """Insert a new backup record within a transaction."""
        query = """
            INSERT INTO backups (
                game_name, filename, filepath, timestamp, size_bytes, file_count,
                trigger_type, status, verification_status, duration_seconds,
                sha256_hash, error_message, manifest_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            record.game_name,
            record.filename,
            record.filepath,
            record.timestamp,
            record.size_bytes,
            record.file_count,
            record.trigger_type,
            record.status,
            record.verification_status,
            record.duration_seconds,
            record.sha256_hash,
            record.error_message,
            record.manifest_json,
        )
        with self._get_connection() as conn:
            cur = conn.execute(query, params)
            record_id = cur.lastrowid
            conn.commit()
            record.id = record_id

            self.add_audit_log(
                "BACKUP_RECORD_CREATED",
                f"Record #{record_id} for {record.game_name} ({record.filename}) - Status: {record.status}"
            )
            return record_id

    def update_backup_status(
        self,
        backup_id: int,
        status: str,
        verification_status: str,
        error_message: Optional[str] = None
    ) -> bool:
        """Update the status and verification outcome of an existing backup."""
        query = """
            UPDATE backups
            SET status = ?, verification_status = ?, error_message = ?
            WHERE id = ?
        """
        with self._get_connection() as conn:
            cur = conn.execute(query, (status, verification_status, error_message, backup_id))
            conn.commit()
            return cur.rowcount > 0

    def get_backup_by_id(self, backup_id: int) -> Optional[BackupRecord]:
        """Fetch a single backup record by primary key."""
        query = "SELECT * FROM backups WHERE id = ?"
        with self._get_connection() as conn:
            row = conn.execute(query, (backup_id,)).fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def get_latest_successful_backup(self, game_name: str) -> Optional[BackupRecord]:
        """Get the most recent successful and verified backup for a given game."""
        query = """
            SELECT * FROM backups
            WHERE game_name = ?
              AND status = ?
              AND verification_status = ?
            ORDER BY id DESC
            LIMIT 1
        """
        with self._get_connection() as conn:
            row = conn.execute(
                query,
                (game_name, BackupStatus.SUCCESS.value, VerificationStatus.VERIFIED_OK.value)
            ).fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def get_all_backups(
        self,
        game_name: Optional[str] = None,
        status_filter: Optional[str] = None,
        limit: int = 200
    ) -> List[BackupRecord]:
        """Retrieve backup records with optional filtering."""
        query = "SELECT * FROM backups WHERE 1=1"
        params: List[object] = []

        if game_name:
            query += " AND game_name = ?"
            params.append(game_name)

        if status_filter and status_filter != "all":
            query += " AND status = ?"
            params.append(status_filter)

        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        records: List[BackupRecord] = []
        with self._get_connection() as conn:
            for row in conn.execute(query, params).fetchall():
                records.append(self._row_to_record(row))
        return records

    def delete_backup(self, backup_id: int) -> bool:
        """Delete a backup record from the database."""
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
            conn.commit()
            self.add_audit_log("BACKUP_RECORD_DELETED", f"Deleted backup ID {backup_id}")
            return cur.rowcount > 0

    def get_total_storage_used(self, game_name: Optional[str] = None) -> int:
        """
        Calculate the real physical disk storage in bytes used by unique existing backup archives.
        Avoids summing duplicate records pointing to the same archive file (e.g. single vault mode).
        """
        query = """
            SELECT filepath, MAX(size_bytes) as recorded_size
            FROM backups
            WHERE status = ?
        """
        params: List[object] = [BackupStatus.SUCCESS.value]
        if game_name:
            query += " AND game_name = ?"
            params.append(game_name)

        query += " GROUP BY filepath"

        total_bytes = 0
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            for r in rows:
                fp_str = r["filepath"]
                if not fp_str:
                    continue
                fp = Path(fp_str)
                try:
                    if fp.is_file():
                        total_bytes += fp.stat().st_size
                except Exception:
                    # Fallback to recorded size if filesystem inaccessible
                    total_bytes += int(r["recorded_size"] or 0)

        return total_bytes

    def reconcile_backups(self, game_name: Optional[str] = None) -> Tuple[int, int]:
        """
        Startup reconciliation:
        1. Checks whether recorded archive files physically exist on disk.
        2. If missing, marks verification_status as VERIFIED_FAIL with explanatory error.
        3. Cleans up any backups left permanently 'in_progress' from a past crash.
        Returns: (reconciled_missing_count, fixed_interrupted_count)
        """
        missing_count = 0
        interrupted_count = 0

        with self._get_connection() as conn:
            # Reconcile any duplicate Witcher 3 records to canonical name
            conn.execute(
                "UPDATE backups SET game_name = 'The Witcher 3: Wild Hunt' WHERE game_name = 'The Witcher 3'"
            )

            # Check interrupted
            cur = conn.execute(
                "UPDATE backups SET status = ?, error_message = ? WHERE status = ?",
                (BackupStatus.INTERRUPTED.value, "Process crashed or terminated during backup.", BackupStatus.IN_PROGRESS.value)
            )
            interrupted_count = cur.rowcount

            # Check physical file existence and recover lingering journals
            from src.backup.zip_writer import recover_vault_from_journal
            query = "SELECT id, filepath FROM backups WHERE status = ?"
            rows = conn.execute(query, (BackupStatus.SUCCESS.value,)).fetchall()
            for r in rows:
                p = Path(r["filepath"])
                journal = p.parent / f"{p.name}.vault_journal"
                if journal.is_file():
                    recover_vault_from_journal(p)
                if not p.is_file():
                    conn.execute(
                        "UPDATE backups SET verification_status = ?, error_message = ? WHERE id = ?",
                        (VerificationStatus.VERIFIED_FAIL.value, "Archive file missing from disk.", r["id"])
                    )
                    missing_count += 1

            conn.commit()

        if missing_count or interrupted_count:
            self.logger.warning(
                "Database reconciliation: marked %d missing archives and %d interrupted records.",
                missing_count, interrupted_count
            )
            self.add_audit_log(
                "RECONCILE_COMPLETED",
                f"Missing: {missing_count}, Interrupted: {interrupted_count}"
            )

        return missing_count, interrupted_count

    def add_audit_log(self, action: str, details: str = "") -> None:
        """Record an administrative audit log event."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO audit_logs (action, details) VALUES (?, ?)",
                    (action, details)
                )
                conn.commit()
        except Exception as e:
            self.logger.error("Failed to write audit log: %s", e)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> BackupRecord:
        return BackupRecord(
            id=row["id"],
            game_name=row["game_name"],
            filename=row["filename"],
            filepath=row["filepath"],
            timestamp=row["timestamp"],
            size_bytes=row["size_bytes"],
            file_count=row["file_count"],
            trigger_type=row["trigger_type"],
            status=row["status"],
            verification_status=row["verification_status"],
            duration_seconds=row["duration_seconds"],
            sha256_hash=row["sha256_hash"],
            error_message=row["error_message"],
            manifest_json=row["manifest_json"],
        )
