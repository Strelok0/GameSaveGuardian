"""
Safe restore system for Game Save Guardian.
Includes Zip Slip prevention, staging verification, and automatic pre-restore safety backups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import shutil
import time
from typing import Callable, Dict, List, Optional, Set, Tuple
import uuid
import zipfile

from src.backup.integrity import is_safe_zip_path, verify_zip_archive
from src.backup.zip_writer import ZipWriter, generate_archive_filename
from src.database import DatabaseManager
from src.models import BackupRecord, FileManifest
from src.utils.logging_config import get_logger

logger = get_logger()


@dataclass
class RestorePreview:
    archive_name: str
    archive_path: str
    total_files: int
    files_to_add: List[str]
    files_to_replace: List[str]
    files_untouched: List[str]


@dataclass
class AutoHealResult:
    checked: bool
    missing_found: int
    restored_files: List[str] = field(default_factory=list)
    failed_files: List[str] = field(default_factory=list)
    message: str = ""


class RestoreManager:
    """Manages secure, verified restoration of game saves."""

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def inspect_archive(self, zip_path: Path | str) -> Tuple[bool, List[dict], str]:
        """
        Inspect files stored inside a ZIP archive without extracting them.
        Returns (success: bool, file_list: List[dict], error_msg: str).
        """
        path = Path(zip_path)
        if not path.is_file():
            return False, [], f"Archive not found: {path}"

        results: List[dict] = []
        try:
            with zipfile.ZipFile(str(path), "r") as zf:
                for info in zf.infolist():
                    if info.filename == "_manifest.json" or info.is_dir():
                        continue
                    results.append({
                        "filename": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "date_time": f"{info.date_time[0]:04d}-{info.date_time[1]:02d}-{info.date_time[2]:02d} {info.date_time[3]:02d}:{info.date_time[4]:02d}:{info.date_time[5]:02d}"
                    })
            return True, results, ""
        except Exception as e:
            return False, [], f"Could not inspect archive: {e}"

    def generate_preview(
        self,
        zip_path: Path | str,
        target_dir: Path | str,
        cached_file_list: Optional[List[dict]] = None
    ) -> Tuple[bool, Optional[RestorePreview], str]:
        """
        Analyze what files will be added or replaced if restored into target_dir.
        Uses in-memory set operations and optional cached file list to ensure high-speed processing
        even on massive archives with thousands of save files.
        """
        target = Path(target_dir).resolve()
        if cached_file_list is not None:
            file_list = cached_file_list
        else:
            ok, file_list, err = self.inspect_archive(zip_path)
            if not ok:
                return False, None, err

        archive_rel_paths: Set[str] = {item["filename"] for item in file_list}

        # Build existing files set with a single directory walk
        existing_target_files: Set[str] = set()
        if target.exists():
            for root, _, files in os.walk(target):
                for f in files:
                    full = Path(root) / f
                    try:
                        rel = full.relative_to(target).as_posix()
                        existing_target_files.add(rel)
                    except ValueError:
                        pass

        # Fast in-memory set membership checks
        files_to_replace: List[str] = [rel for rel in archive_rel_paths if rel in existing_target_files]
        files_to_add: List[str] = [rel for rel in archive_rel_paths if rel not in existing_target_files]
        untouched: List[str] = list(existing_target_files - archive_rel_paths)

        preview = RestorePreview(
            archive_name=Path(zip_path).name,
            archive_path=str(zip_path),
            total_files=len(file_list),
            files_to_add=files_to_add,
            files_to_replace=files_to_replace,
            files_untouched=untouched
        )
        return True, preview, ""

    def create_safety_backup(self, save_dir: Path | str, backup_dest_dir: Path | str) -> Tuple[bool, Optional[Path], str]:
        """
        Creates an emergency safety snapshot of current save directory before overwriting.
        """
        src = Path(save_dir).resolve()
        dest = Path(backup_dest_dir).resolve()

        if not src.exists() or not any(src.iterdir()):
            return True, None, "No existing files to preserve."

        dest.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safety_name = f"Safety_PreRestore_{ts_str}.zip"
        safety_path = dest / safety_name

        try:
            with zipfile.ZipFile(str(safety_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(src):
                    for fname in files:
                        full = Path(root) / fname
                        rel = full.relative_to(src).as_posix()
                        zf.write(str(full), arcname=rel)

            logger.info("Safety backup created before restore: %s", safety_path)
            self.db.add_audit_log("SAFETY_BACKUP_CREATED", f"Created pre-restore backup: {safety_path}")
            return True, safety_path, "Safety backup created."
        except Exception as e:
            logger.error("Failed to create safety backup: %s", e)
            return False, None, f"Could not create safety backup: {e}"

    def execute_restore(
        self,
        zip_path: Path | str,
        target_dir: Path | str,
        is_direct_restore: bool = False,
        backup_dir_for_safety: Optional[Path | str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[bool, str, Optional[Path]]:
        """
        Performs safe extraction:
        1. Verifies ZIP archive integrity.
        2. Zip Slip checks on all members.
        3. If is_direct_restore and existing files found: creates pre-restore safety backup.
        4. Extracts to a temporary staging folder.
        5. Validates staged files.
        6. Atomically copies/moves staged files to target destination.
        Returns: (success: bool, message: str, safety_backup_path: Optional[Path])
        """
        archive_path = Path(zip_path).resolve()
        target = Path(target_dir).resolve()

        if progress_callback:
            progress_callback(5, 100, "Verifying archive integrity (CRC32)...")

        # Step 1: Validate archive format and CRC
        is_valid, v_msg = verify_zip_archive(archive_path)
        if not is_valid:
            return False, f"Archive validation failed: {v_msg}", None

        if progress_callback:
            progress_callback(15, 100, "Checking path safety against Zip Slip...")

        # Step 2: Validate all entries against Zip Slip
        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                for name in zf.namelist():
                    if name == "_manifest.json":
                        continue
                    is_safe, reason = is_safe_zip_path(name, target)
                    if not is_safe:
                        logger.error("Security alert: %s", reason)
                        return False, f"Security rejection: {reason}", None
        except Exception as e:
            return False, f"Failed reading archive headers: {e}", None

        # Step 3: Create safety backup if performing direct restore
        safety_path: Optional[Path] = None
        if is_direct_restore and backup_dir_for_safety:
            if progress_callback:
                progress_callback(20, 100, "Creating safety snapshot of current save files...")
            ok, s_path, s_msg = self.create_safety_backup(target, backup_dir_for_safety)
            if not ok:
                return False, f"Safety backup failed before restore. Operation cancelled for safety: {s_msg}", None
            safety_path = s_path

        # Step 4: Extract to temporary staging folder
        staging_dir = target.parent / f"~restore_staging_{uuid.uuid4().hex[:8]}"
        staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(str(archive_path), "r") as zf:
                members = [
                    m for m in zf.infolist()
                    if m.filename != "_manifest.json" and not m.is_dir()
                ]
                total_members = len(members)

                for idx, member in enumerate(members):
                    is_safe, reason = is_safe_zip_path(member.filename, staging_dir)
                    if not is_safe:
                        raise ValueError(f"Zip slip in staging: {reason}")

                    zf.extract(member, str(staging_dir))

                    if progress_callback and (idx % 25 == 0 or idx == total_members - 1):
                        # Progress range 25% to 75%
                        pct = 25 + int((idx + 1) / max(1, total_members) * 50)
                        progress_callback(pct, 100, f"Extracting saves ({idx + 1}/{total_members})...")

            # Step 5: Verify staged files exist
            if progress_callback:
                progress_callback(78, 100, "Verifying extracted files...")

            staged_files = [f for f in staging_dir.rglob("*") if f.is_file()]
            if not staged_files:
                raise RuntimeError("Staging directory is empty after extraction.")

            # Step 6: Move/copy staged files to target directory
            target.mkdir(parents=True, exist_ok=True)
            total_staged = len(staged_files)
            for jdx, staged_item in enumerate(staged_files):
                rel_p = staged_item.relative_to(staging_dir)
                final_dest_file = target / rel_p
                final_dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(staged_item), str(final_dest_file))

                if progress_callback and (jdx % 25 == 0 or jdx == total_staged - 1):
                    # Progress range 80% to 98%
                    pct = 80 + int((jdx + 1) / max(1, total_staged) * 18)
                    progress_callback(pct, 100, f"Applying saves to folder ({jdx + 1}/{total_staged})...")

            if progress_callback:
                progress_callback(100, 100, "Restoration completed successfully!")

            logger.info("Successfully restored archive %s to %s", archive_path.name, target)
            self.db.add_audit_log(
                "RESTORE_COMPLETED",
                f"Restored {archive_path.name} to {target} (Direct: {is_direct_restore})"
            )
            return True, f"Successfully restored to {target}", safety_path

        except Exception as e:
            logger.error("Restore failed: %s", e, exc_info=True)
            return False, f"Restore failed: {e}", safety_path

        finally:
            # Clean up staging directory
            if staging_dir.exists():
                try:
                    shutil.rmtree(str(staging_dir), ignore_errors=True)
                except Exception:
                    pass

    def check_and_restore_missing_saves(
        self,
        save_dir: Path | str,
        backup_path_or_dir: Path | str,
        game_name: str = "",
        dry_run: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> AutoHealResult:
        """
        Automatically verifies the original game save folder against backup archives.
        If any save files present in the backup are missing in save_dir,
        automatically restores ONLY those missing files without overwriting existing files.
        """
        target = Path(save_dir).resolve()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return AutoHealResult(
                checked=False,
                missing_found=0,
                message=f"Cannot access save directory: {e}"
            )

        b_path = Path(backup_path_or_dir).resolve()
        candidate_zips: List[Path] = []
        if b_path.is_file() and b_path.suffix.lower() == ".zip":
            candidate_zips.append(b_path)
        elif b_path.is_dir():
            from src.backup.zip_writer import get_master_archive_filename
            master_zip = b_path / get_master_archive_filename(game_name)
            if master_zip.is_file():
                candidate_zips.append(master_zip)

            other_zips = sorted(
                [
                    p for p in b_path.glob("*.zip")
                    if not p.name.startswith("~") and not p.name.startswith("Safety_") and p != master_zip
                ],
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            candidate_zips.extend(other_zips)

        if not candidate_zips:
            return AutoHealResult(
                checked=True,
                missing_found=0,
                message="No backup archives found to verify against."
            )

        # Map relative path -> (archive_path, ZipInfo)
        missing_entries: Dict[str, Tuple[Path, zipfile.ZipInfo]] = {}

        for arch_path in candidate_zips:
            try:
                with zipfile.ZipFile(str(arch_path), "r") as zf:
                    for info in zf.infolist():
                        if info.filename == "_manifest.json" or info.is_dir() or info.filename.endswith("/") or info.filename.endswith("\\"):
                            continue

                        # Zip slip prevention
                        is_safe, _ = is_safe_zip_path(info.filename, target)
                        if not is_safe:
                            continue

                        rel_p = info.filename.replace("\\", "/")
                        dest_file = target / rel_p

                        # Crucial condition: ONLY if the file does NOT exist on disk!
                        if dest_file.exists():
                            continue

                        if rel_p not in missing_entries:
                            missing_entries[rel_p] = (arch_path, info)
            except Exception as e:
                logger.warning("Could not inspect archive %s during auto-heal scan: %s", arch_path.name, e)

        total_missing = len(missing_entries)
        if total_missing == 0:
            return AutoHealResult(
                checked=True,
                missing_found=0,
                message="All save files are verified and present in the original folder."
            )

        if dry_run:
            return AutoHealResult(
                checked=True,
                missing_found=total_missing,
                restored_files=list(missing_entries.keys()),
                message=f"Found {total_missing} missing save file(s)."
            )

        # Extract only the missing files
        restored_files: List[str] = []
        failed_files: List[str] = []

        archives_to_extract: Dict[Path, List[zipfile.ZipInfo]] = {}
        for rel_p, (arch_p, info) in missing_entries.items():
            archives_to_extract.setdefault(arch_p, []).append(info)

        current_idx = 0
        for arch_p, infos in archives_to_extract.items():
            try:
                with zipfile.ZipFile(str(arch_p), "r") as zf:
                    for info in infos:
                        dest_file = target / info.filename.replace("\\", "/")
                        # Re-verify it doesn't exist
                        if dest_file.exists():
                            continue

                        try:
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(info, "r") as src_f, open(dest_file, "wb") as dst_f:
                                shutil.copyfileobj(src_f, dst_f)

                            # Restore original timestamp if present
                            if info.date_time:
                                try:
                                    dt_val = time.mktime(datetime(*info.date_time).timetuple())
                                    os.utime(str(dest_file), (dt_val, dt_val))
                                except Exception:
                                    pass

                            restored_files.append(info.filename)
                        except Exception as f_err:
                            logger.error("Failed to restore missing file %s: %s", info.filename, f_err)
                            failed_files.append(info.filename)

                        current_idx += 1
                        if progress_callback:
                            pct = int((current_idx / max(1, total_missing)) * 100)
                            progress_callback(pct, 100, f"Restoring missing save ({current_idx}/{total_missing})...")
            except Exception as arch_err:
                logger.error("Failed opening archive %s for missing files restore: %s", arch_p.name, arch_err)
                for info in infos:
                    if info.filename not in restored_files:
                        failed_files.append(info.filename)

        if restored_files:
            logger.info(
                "Auto-heal restored %d missing save files for '%s' to %s",
                len(restored_files), game_name or "Game", target
            )
            sample_names = ", ".join(restored_files[:3])
            if len(restored_files) > 3:
                sample_names += f" (+{len(restored_files)-3} more)"
            self.db.add_audit_log(
                "AUTO_HEAL_RESTORED",
                f"Auto-healed {len(restored_files)} missing saves for '{game_name}': {sample_names}"
            )

        msg = f"Successfully restored {len(restored_files)} missing save file(s)."
        if failed_files:
            msg += f" ({len(failed_files)} failed)"

        return AutoHealResult(
            checked=True,
            missing_found=total_missing,
            restored_files=restored_files,
            failed_files=failed_files,
            message=msg
        )
