"""
Path management and Windows game save discovery utilities for Game Save Guardian.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import platformdirs

APP_NAME = "GameSaveGuardian"
APP_AUTHOR = "GameSaveGuardian"


def get_app_data_dir() -> Path:
    """Return the application data directory where database and logs are stored."""
    data_dir = Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_logs_dir() -> Path:
    """Return the application logs directory."""
    logs_dir = get_app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_database_path() -> Path:
    """Return the path to the application's SQLite database."""
    return get_app_data_dir() / "guardian.db"


def get_config_path() -> Path:
    """Return the path to the JSON configuration file."""
    return get_app_data_dir() / "config.json"


def get_default_backup_dir(game_name: str = "The Witcher 3") -> Path:
    """Return a safe default backup destination directory."""
    user_home = Path.home()
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in game_name)
    backup_root = user_home / "GameSaveGuardian_Backups" / safe_name
    return backup_root


def get_assets_dir() -> Path:
    """Return the assets directory containing application icons and images."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent
    return base_dir / "assets"


def get_app_icon_path(filename: str = "icon.ico") -> Path:
    """Return the resolved path to an icon or asset file."""
    return get_assets_dir() / filename


def get_windows_documents_dirs() -> List[Path]:
    """
    Find standard and OneDrive-redirected Windows Documents directories.
    Handles standard Windows, OneDrive personal, and OneDrive for Business locations.
    """
    candidates: List[Path] = []
    user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))

    # 1. Standard Documents
    std_docs = user_profile / "Documents"
    if std_docs.exists():
        candidates.append(std_docs)

    # 2. OneDrive Documents
    onedrive_env = os.environ.get("OneDrive")
    if onedrive_env:
        od_docs = Path(onedrive_env) / "Documents"
        if od_docs.exists() and od_docs not in candidates:
            candidates.append(od_docs)

    od_consumer = os.environ.get("OneDriveConsumer")
    if od_consumer:
        od_c_docs = Path(od_consumer) / "Documents"
        if od_c_docs.exists() and od_c_docs not in candidates:
            candidates.append(od_c_docs)

    # Check direct user profile OneDrive subfolder if env not set
    profile_od = user_profile / "OneDrive" / "Documents"
    if profile_od.exists() and profile_od not in candidates:
        candidates.append(profile_od)

    # 3. Windows Shell API via ctypes if on Windows
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 0x0005 (My Documents)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf)
            shell_docs = Path(buf.value)
            if shell_docs.exists() and shell_docs not in candidates:
                candidates.append(shell_docs)
        except Exception:
            pass

    return candidates


def detect_witcher_3_save_paths() -> List[Path]:
    """
    Detect existing The Witcher 3: Wild Hunt save directories.
    Returns a list of verified existing directories.
    """
    doc_dirs = get_windows_documents_dirs()
    found_paths: List[Path] = []

    for doc_dir in doc_dirs:
        candidate = doc_dir / "The Witcher 3" / "gamesaves"
        if candidate.exists() and candidate.is_dir():
            found_paths.append(candidate)

    # Also check without 'gamesaves' in case saves are in root of Witcher 3
    for doc_dir in doc_dirs:
        candidate_root = doc_dir / "The Witcher 3"
        if candidate_root.exists() and candidate_root.is_dir():
            # Check if .sav files exist in root
            sav_files = list(candidate_root.glob("*.sav"))
            if sav_files and candidate_root not in found_paths:
                found_paths.append(candidate_root)

    return found_paths


def is_path_overlapping_or_recursive(path_a: Path | str, path_b: Path | str) -> Tuple[bool, str]:
    """
    Verify whether path_a and path_b are identical or whether one contains the other.
    Returns (is_overlapping, explanation_reason).
    """
    try:
        res_a = Path(path_a).resolve()
        res_b = Path(path_b).resolve()
    except Exception as e:
        return True, f"Invalid path resolution: {e}"

    if res_a == res_b:
        return True, "Source save directory and backup destination directory cannot be the same path."

    try:
        if res_a.is_relative_to(res_b):
            return True, f"Source directory '{res_a}' is inside the backup destination '{res_b}'. This would cause recursive backups."
    except AttributeError:
        # Python < 3.9 fallback if needed
        try:
            res_a.relative_to(res_b)
            return True, f"Source directory '{res_a}' is inside the backup destination '{res_b}'."
        except ValueError:
            pass

    try:
        if res_b.is_relative_to(res_a):
            return True, f"Backup destination '{res_b}' is inside the source directory '{res_a}'. This would cause recursive backups."
    except AttributeError:
        try:
            res_b.relative_to(res_a)
            return True, f"Backup destination '{res_b}' is inside the source directory '{res_a}'."
        except ValueError:
            pass

    return False, ""


def get_disk_free_space(path: Path | str) -> int:
    """
    Return free disk space in bytes for the drive containing path.
    Returns 0 if drive cannot be queried.
    """
    try:
        target = Path(path).resolve()
        # Find existing parent if target doesn't exist yet
        while not target.exists() and target.parent != target:
            target = target.parent
        usage = shutil.disk_usage(str(target))
        return usage.free
    except Exception:
        return 0


def format_bytes(bytes_count: int) -> str:
    """Format bytes count to human-readable string (KB, MB, GB)."""
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


def is_same_physical_disk(path_a: Path | str, path_b: Path | str) -> bool:
    """
    Check if two paths are on the same drive (e.g. both on C:).
    Returns True if on the same drive letter or root mount.
    """
    try:
        drive_a = Path(path_a).resolve().anchor.upper()
        drive_b = Path(path_b).resolve().anchor.upper()
        return bool(drive_a and drive_b and drive_a == drive_b)
    except Exception:
        return False


KNOWN_SAVE_EXTENSIONS = {".sav", ".save", ".dat", ".sl2", ".ess", ".profile"}
SAVE_SUBFOLDERS = {"saves", "gamesaves", "savegames", "savedgames"}
BLACKLIST_DIRS = {
    "logs", "log", "cache", "caches", "shadercache", "shader_cache",
    "crashdumps", "crash_dumps", "temp", "tmp", "directx", "vulkan",
    "webcache", "gpu_cache"
}


def is_fullscreen_window_active() -> bool:
    """
    Check if the active foreground window is fullscreen (likely a running game).
    Uses Win32 user32 API. Safely returns False on error or non-Windows.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN

        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top

        return win_w >= screen_w and win_h >= screen_h
    except Exception:
        return False


KNOWN_GAME_PROCESSES: dict[str, str] = {
    "the witcher 3: wild hunt": "witcher3.exe",
    "the witcher 3": "witcher3.exe",
    "witcher 3": "witcher3.exe",
    "falloutnv": "falloutnv.exe",
    "fallout: new vegas": "falloutnv.exe",
    "fallout 4": "fallout4.exe",
    "skyrim": "tesv.exe",
    "skyrim special edition": "skyrimse.exe",
    "the elder scrolls v: skyrim": "tesv.exe",
    "the elder scrolls v: skyrim special edition": "skyrimse.exe",
    "elden ring": "eldenring.exe",
    "cyberpunk 2077": "cyberpunk2077.exe",
    "dark souls iii": "darksoulsiii.exe",
    "baldur's gate 3": "bg3.exe",
    "starfield": "starfield.exe",
    "hogwarts legacy": "hogwartslegacy.exe",
    "red dead redemption 2": "rdr2.exe",
    "grand theft auto v": "gta5.exe",
}


def auto_detect_all_games(custom_roots: Optional[List[Path]] = None) -> dict[str, Path]:
    """
    Universal multi-game save auto-detector using ultra-fast os.scandir.
    Returns mapping of detected game titles to their respective save directory paths.
    Runs in <50ms by bounding search depth and blacklisting caches/logs.
    """
    detected: dict[str, Path] = {}

    if custom_roots is not None:
        roots_to_scan = [Path(r) for r in custom_roots]
    else:
        user_profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        roots_to_scan = [
            user_profile / "Saved Games",
            user_profile / "Documents",
            user_profile / "Documents" / "My Games",
            user_profile / "AppData" / "LocalLow",
        ]
        # Include OneDrive if present
        for doc_dir in get_windows_documents_dirs():
            if doc_dir not in roots_to_scan:
                roots_to_scan.append(doc_dir)
                mg = doc_dir / "My Games"
                if mg.exists() and mg not in roots_to_scan:
                    roots_to_scan.append(mg)

        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            roots_to_scan.append(Path(local_appdata))

        appdata = os.environ.get("APPDATA")
        if appdata:
            roots_to_scan.append(Path(appdata))

        steam_userdata = Path("C:/Program Files (x86)/Steam/userdata")
        if steam_userdata.exists():
            roots_to_scan.append(steam_userdata)

    GAME_CANONICAL_NAMES = {
        "the witcher 3": "The Witcher 3: Wild Hunt",
        "witcher 3": "The Witcher 3: Wild Hunt",
        "witcher3": "The Witcher 3: Wild Hunt",
    }

    def is_blacklisted(name: str) -> bool:
        low = name.lower()
        if (
            low.startswith(".")
            or low in BLACKLIST_DIRS
            or "shadercache" in low
            or low.startswith("com.")
            or "qoder" in low
            or "electron" in low
            or "cursor" in low
            or "vscode" in low
            or "microsoft" in low
            or "adobe" in low
            or "google" in low
            or "node_modules" in low
        ):
            return True
        return False

    def scan_game_folder(folder_path: Path, game_name: str) -> Optional[Path]:
        """Check if folder_path or any immediate subfolder contains game saves."""
        try:
            with os.scandir(str(folder_path)) as it:
                found_sub: Optional[Path] = None
                has_saves = False
                for e in it:
                    el = e.name.lower()
                    if is_blacklisted(el):
                        continue
                    if e.is_dir():
                        if el in SAVE_SUBFOLDERS:
                            found_sub = Path(e.path)
                    elif e.is_file():
                        _, ext = os.path.splitext(el)
                        if ext in KNOWN_SAVE_EXTENSIONS:
                            has_saves = True
                if found_sub:
                    return found_sub
                if has_saves:
                    return folder_path
        except (PermissionError, OSError):
            pass
        return None

    for root in roots_to_scan:
        if not root.is_dir():
            continue
        try:
            with os.scandir(str(root)) as it:
                for entry in it:
                    if not entry.is_dir() or is_blacklisted(entry.name):
                        continue

                    # Direct check: entry might be a game folder
                    res = scan_game_folder(Path(entry.path), entry.name)
                    if res:
                        canonical_title = GAME_CANONICAL_NAMES.get(entry.name.lower(), entry.name)
                        if canonical_title not in detected:
                            detected[canonical_title] = res
                    else:
                        # Depth 2 check: if entry is container like "My Games"
                        if entry.name.lower() in ("my games", "saved games", "userdata"):
                            try:
                                with os.scandir(entry.path) as sub_it:
                                    for sub_e in sub_it:
                                        if sub_e.is_dir() and not is_blacklisted(sub_e.name):
                                            sub_res = scan_game_folder(Path(sub_e.path), sub_e.name)
                                            if sub_res:
                                                sub_canonical = GAME_CANONICAL_NAMES.get(sub_e.name.lower(), sub_e.name)
                                                if sub_canonical not in detected:
                                                    detected[sub_canonical] = sub_res
                            except (PermissionError, OSError):
                                pass
        except (PermissionError, OSError):
            continue

    return detected
