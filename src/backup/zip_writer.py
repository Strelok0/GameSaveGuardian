"""
ZIP archive writer with atomic staging, crash-safe append journaling,
zero-compression asset bypass, and automated vault compaction.
"""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import struct
from typing import Callable, Optional, Tuple
import uuid
import zipfile

from src.models import FileManifest
from src.utils.logging_config import get_logger

logger = get_logger()

VAULT_JOURNAL_MAGIC = b"GSGJRNL1"
PRE_COMPRESSED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".zip", ".7z", ".lz4"
}


def should_store_without_compression(filename: str | Path) -> bool:
    """Return True if the file is already pre-compressed (e.g. image thumbnails, archives)."""
    ext = Path(filename).suffix.lower()
    return ext in PRE_COMPRESSED_EXTENSIONS


def get_compression_mode(level_str: str) -> int:
    """Map config string to zipfile compression constant."""
    mapping = {
        "stored": zipfile.ZIP_STORED,
        "deflated": zipfile.ZIP_DEFLATED,
        "bzip2": zipfile.ZIP_BZIP2,
    }
    return mapping.get(level_str.lower(), zipfile.ZIP_DEFLATED)


def generate_archive_filename(game_name: str, timestamp: Optional[datetime] = None) -> str:
    """Generate a clean, filesystem-safe timestamped archive name."""
    ts = timestamp or datetime.now()
    clean_game = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in game_name)
    date_part = ts.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{clean_game}_{date_part}.zip"


def get_master_archive_filename(game_name: str) -> str:
    """Generate a consistent master vault archive filename."""
    clean_game = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in game_name)
    return f"{clean_game}_Backup.zip"


def recover_vault_from_journal(vault_path: Path | str) -> bool:
    """
    Check if a lingering .vault_journal exists for the given vault.
    If detected, automatically roll back the interrupted append by truncating
    the vault back to original_size and restoring the Central Directory tail bytes.
    Returns True if a rollback was performed, False otherwise.
    """
    vault = Path(vault_path).resolve()
    journal = vault.parent / f"{vault.name}.vault_journal"
    if not journal.is_file():
        return False

    try:
        data = journal.read_bytes()
        if len(data) < 24 or data[:8] != VAULT_JOURNAL_MAGIC:
            logger.warning("Corrupt or invalid vault journal found at %s. Removing.", journal)
            journal.unlink(missing_ok=True)
            return False

        magic, original_size, cd_offset = struct.unpack("<8sQQ", data[:24])
        tail_bytes = data[24:]

        if vault.is_file():
            with open(str(vault), "r+b") as fp:
                fp.seek(cd_offset)
                fp.write(tail_bytes)
                fp.truncate(original_size)
                fp.flush()
                os.fsync(fp.fileno())

        logger.info("Successfully rolled back vault %s from crash journal to size %d.", vault.name, original_size)
        journal.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.error("Failed to recover vault %s from journal %s: %s", vault.name, journal, e)
        return False


def calculate_vault_dead_space(vault_path: Path | str) -> tuple[int, int, float]:
    """
    Calculate (total_size, dead_bytes, dead_ratio) in the vault ZIP.
    Dead space occurs when duplicate/superseded filenames exist in the archive.
    """
    vault = Path(vault_path).resolve()
    if not vault.is_file():
        return 0, 0, 0.0

    total_size = vault.stat().st_size
    if total_size == 0:
        return 0, 0, 0.0

    try:
        with zipfile.ZipFile(str(vault), "r") as zf:
            infolist = zf.infolist()
            seen: set[str] = set()
            dead_bytes = 0
            # Walk in reverse: first seen is the latest active version
            for info in reversed(infolist):
                if info.filename in seen:
                    dead_bytes += info.compress_size + 30 + len(info.filename)
                else:
                    seen.add(info.filename)

            dead_ratio = dead_bytes / total_size if total_size > 0 else 0.0
            return total_size, dead_bytes, dead_ratio
    except Exception as e:
        logger.warning("Could not calculate dead space for %s: %s", vault.name, e)
        return total_size, 0, 0.0


def compact_vault(
    vault_path: Path | str,
    compression_level: str = "deflated",
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> tuple[bool, int, int]:
    """
    Rebuild the vault archive into a temporary staging ZIP keeping only the latest
    valid entry for each filename, verify integrity, and atomically replace the vault.
    Returns: (success: bool, old_size: int, new_size: int)
    """
    vault = Path(vault_path).resolve()
    if not vault.is_file():
        return False, 0, 0

    old_size = vault.stat().st_size
    dest = vault.parent
    temp_staging = dest / f"~staging_compact_{uuid.uuid4().hex[:8]}_{vault.name}"
    compression = get_compression_mode(compression_level)

    try:
        latest_entries: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(str(vault), "r") as zf_in:
            for info in zf_in.infolist():
                latest_entries[info.filename] = info

            total_items = len(latest_entries)
            with zipfile.ZipFile(str(temp_staging), "w", compression=compression, allowZip64=True) as zf_out:
                for idx, (filename, info) in enumerate(latest_entries.items()):
                    with zf_in.open(info) as entry_fp:
                        data = entry_fp.read()
                        comp_type = zipfile.ZIP_STORED if should_store_without_compression(filename) else compression
                        new_info = zipfile.ZipInfo(filename, info.date_time)
                        new_info.compress_type = comp_type
                        new_info.comment = info.comment
                        new_info.extra = info.extra
                        zf_out.writestr(new_info, data)

                    if progress_callback:
                        progress_callback(idx + 1, total_items, filename)

        from src.backup.integrity import verify_zip_archive
        is_valid, msg = verify_zip_archive(temp_staging, full_crc_check=True)
        if not is_valid:
            logger.error("Compaction failed integrity verification: %s", msg)
            if temp_staging.exists():
                temp_staging.unlink(missing_ok=True)
            return False, old_size, old_size

        temp_staging.replace(vault)
        new_size = vault.stat().st_size
        logger.info(
            "Vault %s compacted successfully: %d bytes -> %d bytes",
            vault.name, old_size, new_size
        )
        return True, old_size, new_size

    except Exception as e:
        logger.error("Exception during vault compaction of %s: %s", vault.name, e)
        if temp_staging.exists():
            temp_staging.unlink(missing_ok=True)
        return False, old_size, old_size


def extract_screenshot_from_zip(archive_path: Path | str, target_rel_path: Optional[str] = None) -> Optional[bytes]:
    """
    Inspect the ZIP central directory for a screenshot (.png/.jpg) associated with
    the save or the latest screenshot in the archive, and read its bytes directly
    from memory without writing to disk.
    """
    arch = Path(archive_path)
    if not arch.is_file():
        return None

    try:
        with zipfile.ZipFile(str(arch), "r") as zf:
            if target_rel_path:
                stem = Path(target_rel_path).stem.lower()
                for info in zf.infolist():
                    name_lower = info.filename.lower()
                    if (name_lower.endswith(".png") or name_lower.endswith(".jpg")) and stem in name_lower:
                        return zf.read(info.filename)

            image_infos = [
                info for info in zf.infolist()
                if (info.filename.lower().endswith(".png") or info.filename.lower().endswith(".jpg"))
                and not info.filename.startswith("_")
            ]
            if image_infos:
                latest_info = image_infos[-1]
                return zf.read(latest_info.filename)
    except Exception as e:
        logger.warning("Could not extract screenshot from %s: %s", arch.name, e)

    return None


class ZipWriter:
    """Safely creates full snapshot ZIP archives and manages Single Vault appends."""

    def __init__(self, compression_level: str = "deflated"):
        self.compression = get_compression_mode(compression_level)

    def append_new_files_to_archive(
        self,
        archive_path: Path | str,
        source_dir: Path | str,
        new_rel_paths: list[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> int:
        """
        Append ONLY newly added files directly into the existing master archive
        with atomic crash-safe journaling and zero-compression bypass for pre-compressed assets.
        Returns number of files successfully appended.
        """
        src = Path(source_dir).resolve()
        arch = Path(archive_path).resolve()
        count = 0

        # Step 1: Crash recovery if prior interrupted journal exists
        recover_vault_from_journal(arch)

        # Step 2: Write atomic sidecar journal before modifying existing archive
        journal_path = arch.parent / f"{arch.name}.vault_journal"
        if arch.is_file() and arch.stat().st_size > 0:
            try:
                original_size = arch.stat().st_size
                with zipfile.ZipFile(str(arch), "r") as zf:
                    cd_offset = zf.start_dir

                with open(str(arch), "rb") as fp:
                    fp.seek(cd_offset)
                    tail_bytes = fp.read()

                journal_tmp = journal_path.with_suffix(".tmp")
                with open(str(journal_tmp), "wb") as jfp:
                    jfp.write(struct.pack("<8sQQ", VAULT_JOURNAL_MAGIC, original_size, cd_offset))
                    jfp.write(tail_bytes)
                    jfp.flush()
                    os.fsync(jfp.fileno())
                journal_tmp.replace(journal_path)
            except Exception as e:
                logger.warning("Could not create vault append journal (%s). Proceeding.", e)

        # Step 3: Perform in-place append
        try:
            with zipfile.ZipFile(str(arch), mode="a", compression=self.compression, allowZip64=True) as zf:
                total_items = len(new_rel_paths)
                for idx, rel_path in enumerate(new_rel_paths):
                    full_path = src / rel_path
                    if full_path.is_file():
                        arcname = rel_path.replace("\\", "/")
                        comp_type = zipfile.ZIP_STORED if should_store_without_compression(full_path) else self.compression
                        zf.write(str(full_path), arcname=arcname, compress_type=comp_type)
                        count += 1
                    if progress_callback:
                        progress_callback(idx + 1, total_items, rel_path)

            # Flush file to disk
            with open(str(arch), "a+b") as fp:
                fp.flush()
                os.fsync(fp.fileno())

            # Step 4: Verify archive and remove journal upon success
            from src.backup.integrity import verify_zip_archive
            is_valid, verify_msg = verify_zip_archive(arch, full_crc_check=False)
            if is_valid:
                if journal_path.exists():
                    journal_path.unlink(missing_ok=True)
            else:
                logger.error("Vault append verification failed: %s. Rolling back from journal.", verify_msg)
                recover_vault_from_journal(arch)
                raise IOError(f"Vault verification failed after append: {verify_msg}")

        except Exception as e:
            if journal_path.exists():
                recover_vault_from_journal(arch)
            raise

        return count

    def rebuild_archive_with_updates(
        self,
        existing_archive_path: Path | str,
        source_dir: Path | str,
        new_rel_paths: list[str],
        modified_rel_paths: list[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[Path, Path]:
        """
        Safely update an archive containing modified files:
        Copies unchanged entries from existing archive to a temporary staging file,
        and writes the new & modified files from source_dir.
        """
        src = Path(source_dir).resolve()
        arch = Path(existing_archive_path).resolve()
        dest = arch.parent

        temp_name = f"~staging_{uuid.uuid4().hex[:8]}_{arch.name}"
        temp_path = dest / temp_name

        modified_set = set(p.replace("\\", "/") for p in modified_rel_paths)
        new_set = set(p.replace("\\", "/") for p in new_rel_paths)

        try:
            with zipfile.ZipFile(str(arch), "r") as zf_in, zipfile.ZipFile(
                str(temp_path), "w", compression=self.compression, allowZip64=True
            ) as zf_out:
                # 1. Copy unchanged entries
                for info in zf_in.infolist():
                    if info.filename == "_manifest.json" or info.filename in modified_set:
                        continue
                    with zf_in.open(info) as f_entry:
                        zf_out.writestr(info, f_entry.read())

                # 2. Write new and modified files from disk
                files_to_write = list(modified_set | new_set)
                total_to_write = len(files_to_write)
                for idx, rel_name in enumerate(files_to_write):
                    full_p = src / rel_name
                    if full_p.is_file():
                        comp_type = zipfile.ZIP_STORED if should_store_without_compression(full_p) else self.compression
                        zf_out.write(str(full_p), arcname=rel_name, compress_type=comp_type)
                    if progress_callback:
                        progress_callback(idx + 1, total_to_write, rel_name)

            return temp_path, arch

        except Exception as e:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise

    def create_snapshot_archive(
        self,
        source_dir: Path | str,
        destination_dir: Path | str,
        manifest: FileManifest,
        archive_name: str,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[Path, Path]:
        """
        Creates ZIP archive at a temporary path in destination_dir.
        Returns: (temp_archive_path, final_archive_path)
        Does NOT rename yet; renaming is deferred until after verification!
        """
        src = Path(source_dir).resolve()
        dest = Path(destination_dir).resolve()
        dest.mkdir(parents=True, exist_ok=True)

        final_path = dest / archive_name
        if final_path.exists():
            stem = final_path.stem
            final_path = dest / f"{stem}_{uuid.uuid4().hex[:6]}.zip"

        temp_name = f"~staging_{uuid.uuid4().hex[:8]}_{final_path.name}"
        temp_path = dest / temp_name

        total_files = len(manifest.files)
        logger.info(
            "Creating snapshot archive for %d files at temporary location %s",
            total_files, temp_path
        )

        try:
            with zipfile.ZipFile(
                str(temp_path),
                mode="w",
                compression=self.compression,
                allowZip64=True
            ) as zf:
                zf.writestr("_manifest.json", manifest.to_json())

                for idx, (rel_path, entry) in enumerate(manifest.files.items()):
                    full_file_path = src / rel_path
                    if full_file_path.is_file():
                        arcname = rel_path.replace("\\", "/")
                        comp_type = zipfile.ZIP_STORED if should_store_without_compression(full_file_path) else self.compression
                        zf.write(str(full_file_path), arcname=arcname, compress_type=comp_type)

                    if progress_callback:
                        progress_callback(idx + 1, total_files, rel_path)

            return temp_path, final_path

        except Exception as e:
            logger.error("Failed during ZIP creation at %s: %s", temp_path, e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise
