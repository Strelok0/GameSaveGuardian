"""
Storage provider abstractions for local drives and future cloud storage backends.
Enables clean extensibility without modifying core backup logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
from pathlib import Path
import shutil
from typing import List, Optional, Tuple

from src.models import BackupRecord
from src.utils.logging_config import get_logger

logger = get_logger()


class StorageProvider(ABC):
    """Abstract base class for backup storage destinations."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """Name of the storage provider (e.g. 'Local Disk', 'External Drive', 'Google Drive')."""
        pass

    @abstractmethod
    def store_archive(self, local_archive_path: Path, archive_name: str) -> Tuple[bool, str]:
        """Store the completed archive in the destination. Returns (success, details/error)."""
        pass

    @abstractmethod
    def list_archives(self) -> List[str]:
        """List filenames of available archives at the destination."""
        pass

    @abstractmethod
    def fetch_archive(self, archive_name: str, target_local_dir: Path) -> Optional[Path]:
        """Fetch an archive from storage to a local path for inspection or restoration."""
        pass

    @abstractmethod
    def delete_archive(self, archive_name: str) -> bool:
        """Delete an archive from storage."""
        pass

    @abstractmethod
    def check_health_and_free_space(self) -> Tuple[bool, int, str]:
        """Returns (is_available, free_bytes, message)."""
        pass


class LocalStorageProvider(StorageProvider):
    """Default storage provider targeting a local or mounted external directory."""

    def __init__(self, target_directory: Path | str):
        self.target_dir = Path(target_directory).resolve()

    def get_provider_name(self) -> str:
        return f"Local Storage ({self.target_dir})"

    def store_archive(self, local_archive_path: Path, archive_name: str) -> Tuple[bool, str]:
        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
            target_path = self.target_dir / archive_name
            if local_archive_path.resolve() != target_path.resolve():
                shutil.copy2(str(local_archive_path), str(target_path))
            return True, str(target_path)
        except Exception as e:
            logger.error("LocalStorageProvider failed to store archive: %s", e)
            return False, str(e)

    def list_archives(self) -> List[str]:
        if not self.target_dir.exists():
            return []
        return [f.name for f in self.target_dir.glob("*.zip") if f.is_file()]

    def fetch_archive(self, archive_name: str, target_local_dir: Path) -> Optional[Path]:
        src_path = self.target_dir / archive_name
        if not src_path.is_file():
            return None
        target_path = target_local_dir / archive_name
        if src_path.resolve() == target_path.resolve():
            return src_path
        shutil.copy2(str(src_path), str(target_path))
        return target_path

    def delete_archive(self, archive_name: str) -> bool:
        src_path = self.target_dir / archive_name
        if src_path.is_file():
            try:
                src_path.unlink()
                return True
            except Exception:
                return False
        return False

    def check_health_and_free_space(self) -> Tuple[bool, int, str]:
        if not self.target_dir.exists():
            try:
                self.target_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return False, 0, f"Cannot access backup directory: {e}"

        try:
            usage = shutil.disk_usage(str(self.target_dir))
            return True, usage.free, "OK"
        except Exception as e:
            return False, 0, f"Disk usage query failed: {e}"
