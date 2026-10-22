"""
Change debouncer with file-stability checks.
Coalesces burst filesystem events and ensures files have finished being written by games.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Callable, Dict, Optional, Set, Tuple

from src.utils.logging_config import get_logger

logger = get_logger()


def verify_file_is_stable(
    file_path: Path | str,
    check_count: int = 2,
    interval_seconds: float = 0.2
) -> bool:
    """
    Checks if a file has finished being written by testing whether its size and mtime
    remain unchanged across check_count successive intervals.
    Also verifies the file can be opened for reading (no exclusive lock by the game).
    """
    p = Path(file_path)
    if not p.is_file():
        return False

    prev_size: Optional[int] = None
    prev_mtime: Optional[float] = None

    for _ in range(check_count):
        try:
            stat = p.stat()
            curr_size = stat.st_size
            curr_mtime = stat.st_mtime

            # Try opening in binary read mode to ensure game has released write lock
            with open(p, "rb") as f:
                f.read(1)

            if prev_size is not None and prev_mtime is not None:
                if curr_size != prev_size or abs(curr_mtime - prev_mtime) > 0.001:
                    logger.debug("File %s still changing (size %d -> %d)", p.name, prev_size, curr_size)
                    return False

            prev_size = curr_size
            prev_mtime = curr_mtime
            time.sleep(interval_seconds)

        except (OSError, PermissionError):
            logger.debug("File %s is locked by game process. Not stable yet.", p.name)
            return False

    return True


class ChangeDebouncer:
    """
    Collects filesystem change signals and triggers a coalesced callback
    only after a quiet settling period (debounce) and file stability check.
    """

    def __init__(
        self,
        debounce_seconds: float = 3.0,
        stability_checks: int = 2,
        callback: Optional[Callable[[], None]] = None
    ):
        self.debounce_seconds = max(0.5, debounce_seconds)
        self.stability_checks = max(1, stability_checks)
        self.callback = callback
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._pending_paths: Set[str] = set()
        self._retry_counts: Dict[str, int] = {}
        self._is_active = True

    def on_file_event(self, file_path: str) -> None:
        """Register an event for a file path and reset the debounce timer."""
        with self._lock:
            if not self._is_active:
                return

            self._pending_paths.add(file_path)

            if self._timer is not None:
                self._timer.cancel()

            self._timer = threading.Timer(self.debounce_seconds, self._on_timer_expired)
            self._timer.daemon = True
            self._timer.start()

    def _on_timer_expired(self) -> None:
        """Triggered after debounce settling time. Runs stability checks."""
        with self._lock:
            if not self._is_active:
                return
            paths_to_check = list(self._pending_paths)
            self._pending_paths.clear()

        # Run stability checks on modified files in background thread
        threading.Thread(
            target=self._run_stability_and_notify,
            args=(paths_to_check,),
            daemon=True
        ).start()

    def _run_stability_and_notify(self, paths: list[str]) -> None:
        """Verify files are stable, retry if changing, then invoke callback."""
        all_stable = True
        for path_str in paths:
            p = Path(path_str)
            # If file was deleted, it's fine, no need to wait for stability
            if not p.exists():
                continue

            retries = self._retry_counts.get(path_str, 0)
            if retries < 2:
                if not verify_file_is_stable(p, check_count=self.stability_checks, interval_seconds=0.2):
                    all_stable = False
                    self._retry_counts[path_str] = retries + 1
                    logger.info("File %s not yet stable (retry %d/2). Rescheduling debounce.", p.name, retries + 1)
                    self.on_file_event(path_str)
                    break
            else:
                # Max retries reached, reset and proceed to prevent infinite loops
                self._retry_counts.pop(path_str, None)

        if all_stable and self.callback:
            self._retry_counts.clear()
            logger.info("Filesystem events settled and stable. Triggering backup.")
            try:
                self.callback()
            except Exception as e:
                logger.error("Error executing debounced backup callback: %s", e)

    def cancel(self) -> None:
        """Cancel any pending debounce timer."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._pending_paths.clear()

    def stop(self) -> None:
        """Stop debouncer completely."""
        with self._lock:
            self._is_active = False
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._pending_paths.clear()
