"""
Tests for archive integrity verification, corrupt ZIP detection,
and Zip Slip path traversal defenses.
"""

from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from src.backup.integrity import (
    build_manifest,
    calculate_sha256,
    is_safe_zip_path,
    verify_zip_archive,
)


class TestIntegrityAndSecurity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="gsg_test_integrity_")
        self.base = Path(self.temp_dir)
        self.save_dir = self.base / "saves"
        self.save_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_verifying_archive_contents_and_hashes(self):
        """Test 10: Successful verification of archive contents and SHA-256 hashes."""
        save1 = self.save_dir / "slot1.sav"
        save1.write_bytes(b"DATA_SLOT_1_SAMPLE_SAVE")
        manifest = build_manifest(self.save_dir, compute_hashes=True)

        zip_path = self.base / "valid.zip"
        with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(str(save1), arcname="slot1.sav")

        is_valid, msg = verify_zip_archive(zip_path, expected_manifest=manifest)
        self.assertTrue(is_valid, f"Verification failed unexpectedly: {msg}")

    def test_detect_corrupt_zip_archives(self):
        """Test 9: Detecting truncated or corrupted ZIP archives."""
        # 1. Truncated ZIP
        corrupt_zip = self.base / "corrupt.zip"
        corrupt_zip.write_bytes(b"PK\x03\x04This is not a real zip archive and is truncated")

        is_valid, msg = verify_zip_archive(corrupt_zip)
        self.assertFalse(is_valid)
        self.assertIn("Corrupted or invalid ZIP format", msg)

        # 2. Corrupted data stream inside ZIP
        save1 = self.save_dir / "slot1.sav"
        save1.write_bytes(b"CORRUPT_PAYLOAD_TEST")
        manifest = build_manifest(self.save_dir, compute_hashes=True)

        tampered_zip = self.base / "tampered.zip"
        with zipfile.ZipFile(str(tampered_zip), "w") as zf:
            # Write mismatched content under the expected name
            zf.writestr("slot1.sav", b"TAMPERED_DIFFERENT_CONTENT")

        is_valid2, msg2 = verify_zip_archive(tampered_zip, expected_manifest=manifest)
        self.assertFalse(is_valid2)
        self.assertIn("SHA-256 checksum mismatch", msg2)

    def test_reject_malicious_zip_paths_zip_slip(self):
        """Test 11: Defending against Zip Slip path traversal exploits."""
        target_restore_dir = self.base / "restore_target"
        target_restore_dir.mkdir()

        # Attack vectors
        malicious_names = [
            "../../Windows/System32/calc.exe",
            "../outside.txt",
            "/absolute/root/file.sav",
            "\\windows\\system32\\evil.dll",
            "C:/evil.bat",
            "D:\\danger.exe",
            "nested/../../escaped.txt"
        ]

        for bad_path in malicious_names:
            is_safe, reason = is_safe_zip_path(bad_path, target_restore_dir)
            self.assertFalse(
                is_safe,
                f"Security check failed to reject malicious path '{bad_path}'! Reason: {reason}"
            )

        # Legitimate paths should be accepted
        legit_paths = [
            "ManualSave_001.sav",
            "gamesaves/AutoSave_002.sav",
            "subfolder/slot/data.bin"
        ]
        for good_path in legit_paths:
            is_safe, reason = is_safe_zip_path(good_path, target_restore_dir)
            self.assertTrue(is_safe, f"Valid path '{good_path}' was incorrectly rejected: {reason}")


if __name__ == "__main__":
    unittest.main()
