"""
Tests for filesystem monitoring, debouncing, and file-stability checks.
"""

from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest

from src.monitoring.change_debouncer import ChangeDebouncer, verify_file_is_stable


class TestMonitoringAndStability(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_mon_")
        self.base = Path(self.temp_dir)
        self.test_file = self.base / "active_game.sav"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_stability_check_on_stable_file(self):
        """Test 8: File that is completely written passes stability verification."""
        self.test_file.write_bytes(b"COMPLETED_GAME_SAVE_STATE")
        is_stable = verify_file_is_stable(self.test_file, check_count=2, interval_seconds=0.1)
        self.assertTrue(is_stable)

    def test_file_stability_detects_ongoing_writes(self):
        """Test 8: File being written to by an active process fails initial stability check."""
        self.test_file.write_bytes(b"INITIAL_CHUNK_")

        # Background thread continuously appends data
        stop_writing = threading.Event()

        def _writer():
            while not stop_writing.is_set():
                try:
                    with open(self.test_file, "ab") as f:
                        f.write(b"EXTRA_DATA_BYTES_")
                except Exception:
                    pass
                time.sleep(0.05)

        t = threading.Thread(target=_writer, daemon=True)
        t.start()

        try:
            is_stable = verify_file_is_stable(self.test_file, check_count=3, interval_seconds=0.1)
            self.assertFalse(is_stable, "File undergoing concurrent writes was falsely marked stable!")
        finally:
            stop_writing.set()
            t.join()

    def test_change_debouncer_coalesces_events(self):
        """Verify that rapid successive events trigger only one debounced callback."""
        callback_count = 0
        event_fired = threading.Event()

        def _on_settle():
            nonlocal callback_count
            callback_count += 1
            event_fired.set()

        debouncer = ChangeDebouncer(debounce_seconds=0.3, stability_checks=1, callback=_on_settle)

        # Fire 5 events rapidly
        for i in range(5):
            self.test_file.write_bytes(f"DATA_{i}".encode("utf-8"))
            debouncer.on_file_event(str(self.test_file))
            time.sleep(0.04)

        # Wait for debounce timer to fire
        event_fired.wait(timeout=2.0)
        debouncer.stop()

        self.assertEqual(callback_count, 1, f"Expected 1 coalesced callback, got {callback_count}")


if __name__ == "__main__":
    unittest.main()
