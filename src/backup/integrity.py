"""
Cryptographic integrity checking, manifest generation, and ZIP validation.
Includes Zip Slip and path traversal defenses.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, Optional, Tuple
import zipfile

from src.models import FileEntry, FileManifest
from src.utils.logging_config import get_logger

logger = get_logger()
CHUNK_SIZE = 64 * 1024  # 64 KB streaming chunks


def calculate_sha256(file_path: Path | str) -> str:
    """Calculate the SHA-256 checksum of a file efficiently using chunks."""
    h = hashlib.sha256()
    path = Path(file_path)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    source_dir: Path | str,
    compute_hashes: bool = False,
    cached_manifest: Optional[FileManifest] = None
) -> FileManifest:
    """
    Recursively scan source_dir using fast os.scandir and create a FileManifest.
    Preserves relative paths with forward slashes for cross-platform archive consistency.
    Uses size and mtime for zero-CPU instant change detection.
    """
    src = Path(source_dir).resolve()
    from datetime import datetime
    manifest = FileManifest(timestamp=datetime.now().isoformat())

    if not src.exists() or not src.is_dir():
        return manifest

    stack = [src]
    while stack:
        curr = stack.pop()
        try:
            with os.scandir(curr) as it:
                for entry in it:
                    try:
                        if entry.name.endswith(".tmp") or entry.name.startswith("~"):
                            continue
                        if entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            rel_path = Path(entry.path).relative_to(src).as_posix()

                            sha256_hash = ""
                            if compute_hashes:
                                if (
                                    cached_manifest
                                    and rel_path in cached_manifest.files
                                    and cached_manifest.files[rel_path].size == stat.st_size
                                    and abs(cached_manifest.files[rel_path].mtime - stat.st_mtime) < 0.001
                                    and cached_manifest.files[rel_path].sha256
                                ):
                                    sha256_hash = cached_manifest.files[rel_path].sha256
                                else:
                                    sha256_hash = calculate_sha256(entry.path)

                            file_entry = FileEntry(
                                rel_path=rel_path,
                                size=stat.st_size,
                                mtime=stat.st_mtime,
                                sha256=sha256_hash
                            )
                            manifest.add_entry(file_entry)
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except (OSError, PermissionError):
                        pass
        except (OSError, PermissionError):
            pass

    return manifest


def verify_zip_archive(
    zip_path: Path | str,
    expected_manifest: Optional[FileManifest] = None,
    full_crc_check: bool = True
) -> Tuple[bool, str]:
    """
    ZIP integrity check:
    1. Validates ZIP headers and TOC.
    2. Runs full CRC32 testzip() when full_crc_check=True.
    3. If expected_manifest provided with hashes: verifies stream SHA-256.
    """
    z_path = Path(zip_path)
    if not z_path.is_file():
        return False, f"Archive file does not exist: {z_path}"

    if z_path.stat().st_size == 0:
        return False, "Archive file is empty (0 bytes)."

    try:
        with zipfile.ZipFile(str(z_path), "r") as zf:
            if full_crc_check:
                bad_file = zf.testzip()
                if bad_file:
                    return False, f"ZIP archive CRC32 check failed on entry: {bad_file}"
            else:
                _ = zf.namelist()

            archive_names = set(zf.namelist())

            # 2. Check against manifest if provided
            if expected_manifest:
                for rel_path, entry in expected_manifest.files.items():
                    clean_rel = rel_path.replace("\\", "/")
                    if clean_rel not in archive_names and rel_path not in archive_names:
                        return False, f"Missing expected file in archive: {rel_path}"

                    if entry.sha256:
                        with zf.open(clean_rel if clean_rel in archive_names else rel_path) as z_file:
                            h = hashlib.sha256()
                            while True:
                                chunk = z_file.read(CHUNK_SIZE)
                                if not chunk:
                                    break
                                h.update(chunk)
                            stream_sha = h.hexdigest()

                        if stream_sha != entry.sha256:
                            return False, f"SHA-256 checksum mismatch for {rel_path} in archive"

        return True, "Archive verified successfully."

    except zipfile.BadZipFile as e:
        return False, f"Corrupted or invalid ZIP format: {e}"
    except Exception as e:
        return False, f"Verification failed with unexpected error: {e}"


def is_safe_zip_path(archive_member_name: str, target_base_dir: Path | str) -> Tuple[bool, str]:
    """
    Prevent Zip Slip / Path Traversal attacks.
    Ensures extracting archive_member_name cannot escape target_base_dir.
    """
    base = Path(target_base_dir).resolve()
    
    # 1. Reject drive specifications (e.g. C:, D:)
    if (len(archive_member_name) >= 2 and archive_member_name[1] == ":") or (archive_member_name.startswith(("\\\\", "//"))):
        return False, f"Unsafe path: absolute drive or UNC path detected: {archive_member_name}"

    # 2. Reject absolute root paths
    if archive_member_name.startswith(("/", "\\")):
        return False, f"Unsafe path: leading root slash detected: {archive_member_name}"

    # 3. Resolve destination
    try:
        dest_path = (base / archive_member_name).resolve()
        if not dest_path.is_relative_to(base):
            return False, f"Path traversal detected! Entry escapes target directory: {archive_member_name}"
    except AttributeError:
        try:
            (base / archive_member_name).resolve().relative_to(base)
        except ValueError:
            return False, f"Path traversal detected: {archive_member_name}"
    except Exception as e:
        return False, f"Invalid archive path resolution: {e}"

    return True, ""
