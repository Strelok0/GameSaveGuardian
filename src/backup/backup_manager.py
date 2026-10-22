"""
Core Backup Engine coordinator for Game Save Guardian.
Performs atomic snapshots, integrity validation, change detection, and retention pruning.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Callable, Optional, Tuple
import zipfile

from src.backup.integrity import build_manifest, calculate_sha256, verify_zip_archive
from src.backup.retention import RetentionManager, check_and_compact_vault_if_needed
from src.backup.zip_writer import (
    ZipWriter,
    generate_archive_filename,
    get_master_archive_filename,
    recover_vault_from_journal,
)
from src.database import DatabaseManager
from src.models import (
    AppSettings,
    ArchiveMode,
    BackupRecord,
    BackupStatus,
    BackupTrigger,
    FileManifest,
    VerificationStatus,
)
from src.utils.logging_config import get_logger
from src.utils.paths import get_disk_free_space, is_path_overlapping_or_recursive


class BackupManager:
    """Orchestrates backup snapshots, validations, and storage management."""

    def __init__(self, db_manager: DatabaseManager, settings: AppSettings):
        self.db = db_manager
        self.settings = settings
        self.logger = get_logger()
        self._lock = threading.Lock()
        self._latest_manifest: Optional[FileManifest] = None
        self._latest_manifests: dict[str, FileManifest] = {}

    def execute_backup(
        self,
        trigger: BackupTrigger = BackupTrigger.MANUAL,
        force: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        game_profile: Optional[GameProfile] = None
    ) -> Tuple[bool, str, Optional[BackupRecord]]:
        """
        Executes a complete, independently restorable snapshot backup.
        Returns: (success: bool, message: str, record: Optional[BackupRecord])
        """
        if not self._lock.acquire(blocking=False):
            return False, "A backup operation is already running.", None

        start_time = time.time()
        active_game = game_profile if game_profile is not None else self.settings.active_game
        source_dir = Path(active_game.save_directory).resolve()
        backup_dir = self.settings.get_backup_dir_for_game(active_game).resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Path validations
            if not source_dir.exists() or not source_dir.is_dir():
                return False, f"Source save directory does not exist: {source_dir}", None

            # Check recursion/overlap
            is_overlap, reason = is_path_overlapping_or_recursive(source_dir, backup_dir)
            if is_overlap:
                self.logger.error("Recursive backup error: %s", reason)
                return False, f"Configuration Error: {reason}", None

            # Retrieve latest manifest from memory cache or DB
            cached_manifest = self._latest_manifests.get(active_game.name)
            if cached_manifest is None and self._latest_manifest and self.settings.active_game.name == active_game.name:
                cached_manifest = self._latest_manifest

            if cached_manifest is None:
                last_rec = self.db.get_latest_successful_backup(active_game.name)
                if last_rec and last_rec.manifest_json:
                    try:
                        cached_manifest = FileManifest.from_json(last_rec.manifest_json)
                        self._latest_manifests[active_game.name] = cached_manifest
                    except Exception:
                        pass

            # 2. Build current manifest & change detection (fast scan using file metadata)
            current_manifest = build_manifest(source_dir, compute_hashes=False, cached_manifest=cached_manifest)
            if current_manifest.file_count == 0:
                return False, f"No eligible save files found in {source_dir}", None

            is_single_vault = (self.settings.archive_mode == ArchiveMode.SINGLE_VAULT.value)
            writer = ZipWriter(compression_level=self.settings.compression_level)
            backup_dir.mkdir(parents=True, exist_ok=True)

            # --- SINGLE MASTER ARCHIVE MODE (Append & Sync New Files Only) ---
            if is_single_vault:
                master_filename = get_master_archive_filename(active_game.name)
                final_path = backup_dir / master_filename

                # If master archive does not exist yet: adopt latest existing backup if present
                if not final_path.exists():
                    existing_zips = sorted(
                        [p for p in backup_dir.glob("*.zip") if not p.name.startswith("~") and p.name != master_filename],
                        key=lambda p: p.stat().st_mtime,
                        reverse=True
                    )
                    if existing_zips:
                        latest_zip = existing_zips[0]
                        self.logger.info("Adopting existing backup %s as master vault: %s", latest_zip.name, master_filename)
                        try:
                            shutil.copy2(str(latest_zip), str(final_path))
                        except Exception as e:
                            self.logger.warning("Could not copy existing backup: %s", e)

                # Recover from any interrupted append journal before reading
                recover_vault_from_journal(final_path)

                # If master archive exists: inspect existing members
                if final_path.exists() and final_path.is_file():
                    existing_members = {}
                    try:
                        with zipfile.ZipFile(str(final_path), "r") as zf:
                            for info in zf.infolist():
                                if info.filename != "_manifest.json" and not info.is_dir():
                                    existing_members[info.filename] = info.file_size
                    except Exception as e:
                        self.logger.warning("Existing master archive could not be read (%s). Recreating.", e)
                        final_path = None

                if final_path and final_path.exists():
                    new_files: list[str] = []
                    modified_files: list[str] = []

                    for rel_p, entry in current_manifest.files.items():
                        norm_p = rel_p.replace("\\", "/")
                        if norm_p not in existing_members:
                            new_files.append(rel_p)
                        else:
                            if entry.size != existing_members[norm_p]:
                                modified_files.append(rel_p)

                    # If no new or modified files, skip redundant write
                    if len(new_files) == 0 and len(modified_files) == 0:
                        self.logger.info("Vault %s already contains all save files. Vault is up to date.", final_path.name)
                        if not force:
                            return True, "No changes detected. Vault is up to date.", None

                        archive_size = final_path.stat().st_size
                        duration = round(time.time() - start_time, 2)
                        with zipfile.ZipFile(str(final_path), "r") as zf:
                            total_vault_files = len([n for n in zf.namelist() if n != "_manifest.json" and not n.endswith("/")])

                        rec = BackupRecord(
                            game_name=active_game.name,
                            filename=final_path.name,
                            filepath=str(final_path),
                            timestamp=datetime.now().isoformat(),
                            size_bytes=archive_size,
                            file_count=total_vault_files,
                            trigger_type=trigger.value,
                            status=BackupStatus.SUCCESS.value,
                            verification_status=VerificationStatus.VERIFIED_OK.value,
                            duration_seconds=duration,
                            error_message="Up to date (0 new files)",
                            manifest_json=current_manifest.to_json(),
                        )
                        rec_id = self.db.add_backup(rec)
                        rec.id = rec_id
                        self._latest_manifests[active_game.name] = current_manifest
                        self._latest_manifest = current_manifest
                        return True, "Vault is up to date (0 new files).", rec

                    self.logger.info(
                        "Updating vault %s: adding %d new files, updating %d modified files",
                        final_path.name, len(new_files), len(modified_files)
                    )

                    # Append or Rebuild
                    if len(modified_files) == 0:
                        # Lightning-fast in-place append for newly added files!
                        writer.append_new_files_to_archive(
                            archive_path=final_path,
                            source_dir=source_dir,
                            new_rel_paths=new_files,
                            progress_callback=progress_callback
                        )
                    else:
                        temp_p, _ = writer.rebuild_archive_with_updates(
                            existing_archive_path=final_path,
                            source_dir=source_dir,
                            new_rel_paths=new_files,
                            modified_rel_paths=modified_files,
                            progress_callback=progress_callback
                        )
                        temp_p.replace(final_path)

                    # Fast header and TOC integrity check (avoids decompressing gigabytes on minor appends)
                    is_valid, verify_msg = verify_zip_archive(final_path, full_crc_check=False)
                    if not is_valid:
                        return False, f"Vault integrity verification failed: {verify_msg}", None

                    # Count total entries in updated vault
                    with zipfile.ZipFile(str(final_path), "r") as zf:
                        total_vault_files = len([n for n in zf.namelist() if n != "_manifest.json" and not n.endswith("/")])

                    archive_size = final_path.stat().st_size
                    duration = round(time.time() - start_time, 2)
                    summary = f"+{len(new_files)} new files" if len(new_files) > 0 else f"{len(modified_files)} updated"
                    if len(new_files) > 0 and len(modified_files) > 0:
                        summary = f"+{len(new_files)} new, {len(modified_files)} updated"

                    # Record update in database
                    rec = BackupRecord(
                        game_name=active_game.name,
                        filename=final_path.name,
                        filepath=str(final_path),
                        timestamp=datetime.now().isoformat(),
                        size_bytes=archive_size,
                        file_count=total_vault_files,
                        trigger_type=trigger.value,
                        status=BackupStatus.SUCCESS.value,
                        verification_status=VerificationStatus.VERIFIED_OK.value,
                        duration_seconds=duration,
                        error_message=summary,
                        manifest_json=current_manifest.to_json(),
                    )
                    rec_id = self.db.add_backup(rec)
                    rec.id = rec_id
                    self._latest_manifests[active_game.name] = current_manifest
                    self._latest_manifest = current_manifest

                    self.logger.info("Vault updated in %.2fs: %s (%s)", duration, final_path.name, summary)

                    # Automated compaction for dead space (>25%)
                    try:
                        check_and_compact_vault_if_needed(final_path)
                    except Exception as e:
                        self.logger.warning("Automated compaction check failed: %s", e)

                    return True, f"Updated {final_path.name} ({summary})", rec

            # --- TIMESTAMPED SNAPSHOTS OR INITIAL CREATION ---
            # Incremental check: Skip if unchanged and not forced
            if not force and not current_manifest.has_changed_from(self._latest_manifest):
                self.logger.info("Save directory has not changed. Skipping redundant backup.")
                return True, "No changes detected. Redundant backup skipped.", None

            # Disk space safety check
            required_space = max(current_manifest.total_size * 2, 50 * 1024 * 1024)
            free_space = get_disk_free_space(backup_dir)
            if free_space < required_space:
                err = (
                    f"Insufficient disk space on backup destination. "
                    f"Free: {free_space // (1024*1024)} MB, Required: {required_space // (1024*1024)} MB."
                )
                self.logger.error(err)
                return False, err, None

            # Generate filename
            ts_now = datetime.now()
            if is_single_vault:
                archive_filename = get_master_archive_filename(active_game.name)
            else:
                archive_filename = generate_archive_filename(active_game.name, ts_now)

            # Record initial entry in database
            initial_record = BackupRecord(
                game_name=active_game.name,
                filename=archive_filename,
                filepath=str(backup_dir / archive_filename),
                timestamp=ts_now.isoformat(),
                size_bytes=0,
                file_count=current_manifest.file_count,
                trigger_type=trigger.value,
                status=BackupStatus.IN_PROGRESS.value,
                verification_status=VerificationStatus.UNVERIFIED.value,
                duration_seconds=0.0,
                manifest_json=current_manifest.to_json(),
            )
            record_id = self.db.add_backup(initial_record)

            # Create temporary staging ZIP
            temp_path, final_path = writer.create_snapshot_archive(
                source_dir=source_dir,
                destination_dir=backup_dir,
                manifest=current_manifest,
                archive_name=archive_filename,
                progress_callback=progress_callback
            )

            # Verify ZIP archive integrity BEFORE promoting
            is_valid, verify_msg = verify_zip_archive(temp_path, expected_manifest=current_manifest)
            if not is_valid:
                err_msg = f"Archive integrity verification failed: {verify_msg}"
                self.logger.error(err_msg)
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

                self.db.update_backup_status(
                    record_id,
                    status=BackupStatus.FAILED.value,
                    verification_status=VerificationStatus.VERIFIED_FAIL.value,
                    error_message=err_msg
                )
                return False, err_msg, None

            # Atomic rename: promote temporary staging file to final archive name
            if final_path.exists():
                final_path.unlink()
            temp_path.replace(final_path)

            archive_sha256 = calculate_sha256(final_path)
            archive_size = final_path.stat().st_size
            duration = round(time.time() - start_time, 2)

            self.db.update_backup_status(
                record_id,
                status=BackupStatus.SUCCESS.value,
                verification_status=VerificationStatus.VERIFIED_OK.value,
            )

            completed_record = self.db.get_backup_by_id(record_id)
            if completed_record:
                completed_record.size_bytes = archive_size
                completed_record.sha256_hash = archive_sha256
                completed_record.duration_seconds = duration
                with self.db._get_connection() as conn:
                    conn.execute(
                        "UPDATE backups SET size_bytes = ?, sha256_hash = ?, duration_seconds = ? WHERE id = ?",
                        (archive_size, archive_sha256, duration, record_id)
                    )
                    conn.commit()

            self._latest_manifests[active_game.name] = current_manifest
            self._latest_manifest = current_manifest
            self.logger.info(
                "Backup completed successfully: %s (%d files, %d bytes in %.2fs)",
                final_path.name, current_manifest.file_count, archive_size, duration
            )

            if not is_single_vault and self.settings.retention.enabled:
                try:
                    all_recs = self.db.get_all_backups(game_name=active_game.name)
                    ret_mgr = RetentionManager(self.settings.retention)
                    ret_mgr.apply_retention(all_recs, self.db, protected_ids={record_id})
                except Exception as e:
                    self.logger.warning("Retention pruning error: %s", e)

            return True, f"Backup created successfully: {final_path.name}", completed_record

        except Exception as e:
            self.logger.error("Unexpected error during backup: %s", e, exc_info=True)
            return False, f"Backup failed: {e}", None

        finally:
            self._lock.release()
