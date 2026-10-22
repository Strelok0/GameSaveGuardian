"""
Domain models and dataclasses for Game Save Guardian.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class BackupTrigger(str, Enum):
    REALTIME = "realtime"
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    INITIAL = "initial"


class BackupStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    IN_PROGRESS = "in_progress"


class VerificationStatus(str, Enum):
    VERIFIED_OK = "verified_ok"
    VERIFIED_FAIL = "verified_fail"
    UNVERIFIED = "unverified"


class ProtectionMode(str, Enum):
    HYBRID = "hybrid"
    REALTIME = "realtime"
    SCHEDULED = "scheduled"


class ArchiveMode(str, Enum):
    SINGLE_VAULT = "single_vault"  # Append new files to a single cumulative archive
    SNAPSHOTS = "snapshots"        # Create separate timestamped ZIP snapshots


@dataclass
class FileEntry:
    rel_path: str
    size: int
    mtime: float
    sha256: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileEntry:
        return cls(**data)


@dataclass
class FileManifest:
    timestamp: str
    files: Dict[str, FileEntry] = field(default_factory=dict)
    total_size: int = 0
    file_count: int = 0

    def add_entry(self, entry: FileEntry) -> None:
        self.files[entry.rel_path] = entry
        self.file_count = len(self.files)
        self.total_size = sum(f.size for f in self.files.values())

    def has_changed_from(self, previous: Optional[FileManifest]) -> bool:
        """
        Check if current manifest has any additions, deletions, or modifications
        compared to the previous manifest.
        """
        if previous is None:
            return bool(self.files)

        if set(self.files.keys()) != set(previous.files.keys()):
            return True

        for rel_path, entry in self.files.items():
            prev_entry = previous.files.get(rel_path)
            if prev_entry is None:
                return True
            # Fast check: size or mtime difference
            if entry.size != prev_entry.size or abs(entry.mtime - prev_entry.mtime) > 0.001:
                return True
            # Hash check if available
            if entry.sha256 and prev_entry.sha256 and entry.sha256 != prev_entry.sha256:
                return True

        return False

    def to_json(self) -> str:
        data = {
            "timestamp": self.timestamp,
            "total_size": self.total_size,
            "file_count": self.file_count,
            "files": {k: v.to_dict() for k, v in self.files.items()}
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> FileManifest:
        data = json.loads(json_str)
        manifest = cls(
            timestamp=data.get("timestamp", ""),
            total_size=data.get("total_size", 0),
            file_count=data.get("file_count", 0),
        )
        for k, v in data.get("files", {}).items():
            manifest.files[k] = FileEntry.from_dict(v)
        return manifest


@dataclass
class BackupRecord:
    id: Optional[int] = None
    game_name: str = ""
    filename: str = ""
    filepath: str = ""
    timestamp: str = ""
    size_bytes: int = 0
    file_count: int = 0
    trigger_type: str = BackupTrigger.MANUAL.value
    status: str = BackupStatus.SUCCESS.value
    verification_status: str = VerificationStatus.VERIFIED_OK.value
    duration_seconds: float = 0.0
    sha256_hash: str = ""
    error_message: Optional[str] = None
    manifest_json: Optional[str] = None

    def exists_on_disk(self) -> bool:
        return bool(self.filepath and Path(self.filepath).is_file())


@dataclass
class RetentionPolicy:
    enabled: bool = True
    max_count: int = 30
    keep_all_days: int = 7
    daily_snapshot_days: int = 30

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RetentionPolicy:
        return cls(**data)


@dataclass
class GameProfile:
    name: str = "The Witcher 3: Wild Hunt"
    save_directory: str = ""
    backup_directory: str = ""
    process_name: Optional[str] = None
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GameProfile:
        return cls(
            name=data.get("name", "The Witcher 3: Wild Hunt"),
            save_directory=data.get("save_directory", ""),
            backup_directory=data.get("backup_directory", ""),
            process_name=data.get("process_name"),
            enabled=data.get("enabled", True),
        )


# Backward compatibility alias
GameConfig = GameProfile


@dataclass
class AppSettings:
    active_game: GameProfile = field(default_factory=GameProfile)
    games: List[GameProfile] = field(default_factory=lambda: [GameProfile()])
    backup_mode: str = ProtectionMode.HYBRID.value
    archive_mode: str = ArchiveMode.SINGLE_VAULT.value
    scheduled_interval_minutes: int = 10
    debounce_seconds: float = 3.0
    stability_check_count: int = 3
    compression_level: str = "deflated"  # "stored", "deflated"
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    use_unified_backup_dir: bool = False
    unified_backup_directory: str = str(Path.home() / "GameSaveGuardian_Backups")
    start_with_windows: bool = False
    minimize_to_tray: bool = True
    theme: str = "dark"
    language: str = "en"
    notifications_enabled: bool = True
    silent_gaming_mode: bool = True
    verbose_logging: bool = False
    first_run_completed: bool = False

    def get_active_game(self) -> GameProfile:
        return self.active_game

    def get_backup_dir_for_game(self, game: Union[str, GameProfile]) -> Path:
        """
        Return the resolved backup directory for a game.
        When unified root backup mode is enabled, creates/returns:
            <unified_backup_directory>/<Game_Name>/
        Otherwise returns the game's specific backup_directory or default.
        """
        name = game.name if isinstance(game, GameProfile) else str(game)
        if self.use_unified_backup_dir and self.unified_backup_directory:
            safe_name = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in name).strip()
            return Path(self.unified_backup_directory) / safe_name

        if isinstance(game, GameProfile) and game.backup_directory:
            return Path(game.backup_directory)

        from src.utils.paths import get_default_backup_dir
        return get_default_backup_dir(name)

    def set_active_game(self, name: str) -> bool:
        """Switch active game to profile with the given name, ensuring shared reference."""
        for g in self.games:
            if g.name == name:
                self.active_game = g
                return True
        # If active_game already has this name, ensure it is in games
        if self.active_game.name == name:
            if self.active_game not in self.games:
                self.games.append(self.active_game)
            return True
        return False

    def add_game_profile(self, profile: GameProfile) -> None:
        """Add or update a game profile and keep active_game synchronized."""
        for idx, g in enumerate(self.games):
            if g.name == profile.name:
                # Merge fields: preserve non-empty values
                if profile.save_directory:
                    g.save_directory = profile.save_directory
                if profile.backup_directory:
                    g.backup_directory = profile.backup_directory
                if profile.process_name:
                    g.process_name = profile.process_name
                g.enabled = profile.enabled
                if self.active_game.name == profile.name:
                    self.active_game = g
                return

        self.games.append(profile)
        if self.active_game.name == profile.name:
            self.active_game = profile

    def update_game_profile(
        self,
        name: str,
        save_directory: Optional[str] = None,
        backup_directory: Optional[str] = None,
        process_name: Optional[str] = None,
        new_name: Optional[str] = None,
    ) -> None:
        """Update game profile attributes safely across games and active_game."""
        target: Optional[GameProfile] = None
        for g in self.games:
            if g.name == name:
                target = g
                break

        if target is None:
            target = GameProfile(name=name)
            self.games.append(target)

        if save_directory is not None and save_directory.strip():
            target.save_directory = save_directory.strip()
        if backup_directory is not None and backup_directory.strip():
            target.backup_directory = backup_directory.strip()
        if process_name is not None:
            target.process_name = process_name.strip() or None
        if new_name and new_name.strip():
            target.name = new_name.strip()

        if self.active_game.name == name or self.active_game.name == target.name:
            self.active_game = target

    def delete_game_profile(self, name: str) -> bool:
        """Remove a game profile by name, ensuring at least one profile remains."""
        if len(self.games) <= 1:
            return False

        matching = [g for g in self.games if g.name == name]
        if not matching:
            return False

        self.games = [g for g in self.games if g.name != name]
        if self.active_game.name == name:
            self.active_game = self.games[0]
        return True

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AppSettings:
        from src.utils.paths import detect_witcher_3_save_paths, get_default_backup_dir

        active_game_data = data.get("active_game", {})
        active_game = GameProfile.from_dict(active_game_data) if active_game_data else GameProfile()

        games_list: List[GameProfile] = []
        for g in data.get("games", []):
            games_list.append(GameProfile.from_dict(g))

        # Deduplicate Witcher 3 profiles (merge "The Witcher 3" into "The Witcher 3: Wild Hunt")
        w3_wild_hunt: Optional[GameProfile] = None
        w3_short: Optional[GameProfile] = None
        for g in games_list:
            clean_low = g.name.strip().lower()
            if clean_low in ("the witcher 3: wild hunt", "witcher 3: wild hunt"):
                w3_wild_hunt = g
            elif clean_low in ("the witcher 3", "witcher 3"):
                w3_short = g

        if w3_wild_hunt and w3_short:
            if not w3_wild_hunt.save_directory and w3_short.save_directory:
                w3_wild_hunt.save_directory = w3_short.save_directory
            if not w3_wild_hunt.backup_directory and w3_short.backup_directory:
                w3_wild_hunt.backup_directory = w3_short.backup_directory
            if not w3_wild_hunt.process_name and w3_short.process_name:
                w3_wild_hunt.process_name = w3_short.process_name
            games_list = [g for g in games_list if g is not w3_short]
            if active_game.name.strip().lower() in ("the witcher 3", "witcher 3"):
                active_game = w3_wild_hunt

        # Filter out non-game auto-detected apps (like com.qoder.app.stable, vscode, etc.)
        def is_non_game_app(name: str) -> bool:
            low = name.strip().lower()
            return (
                low.startswith("com.")
                or "qoder" in low
                or "electron" in low
                or "cursor" in low
                or "vscode" in low
                or "node_modules" in low
            )
        games_list = [g for g in games_list if not is_non_game_app(g.name)]
        if is_non_game_app(active_game.name) and games_list:
            active_game = games_list[0]

        # Deduplicate identical names preserving order
        unique_games: List[GameProfile] = []
        seen_names = set()
        for g in games_list:
            if g.name not in seen_names:
                unique_games.append(g)
                seen_names.add(g.name)
        games_list = unique_games

        # Reconcile active_game and games_list to guarantee a shared instance
        matched_in_games: Optional[GameProfile] = None
        for g in games_list:
            if g.name == active_game.name:
                matched_in_games = g
                break

        if matched_in_games is not None:
            # Sync paths bidirectionally so neither source loses its directory
            if active_game.save_directory and not matched_in_games.save_directory:
                matched_in_games.save_directory = active_game.save_directory
            elif matched_in_games.save_directory and not active_game.save_directory:
                active_game.save_directory = matched_in_games.save_directory

            if active_game.backup_directory and not matched_in_games.backup_directory:
                matched_in_games.backup_directory = active_game.backup_directory
            elif matched_in_games.backup_directory and not active_game.backup_directory:
                active_game.backup_directory = matched_in_games.backup_directory

            active_game = matched_in_games
        else:
            if not games_list:
                games_list = [active_game]
            else:
                games_list.insert(0, active_game)

        # Self-healing check for any game missing save or backup dir
        for profile in games_list:
            if not profile.backup_directory:
                profile.backup_directory = str(get_default_backup_dir(profile.name))
            if not profile.save_directory and "witcher" in profile.name.lower():
                w3_detected = detect_witcher_3_save_paths()
                if w3_detected:
                    profile.save_directory = str(w3_detected[0])

        ret_data = data.get("retention", {})
        retention = RetentionPolicy.from_dict(ret_data) if ret_data else RetentionPolicy()

        return cls(
            active_game=active_game,
            games=games_list,
            backup_mode=data.get("backup_mode", ProtectionMode.HYBRID.value),
            archive_mode=data.get("archive_mode", ArchiveMode.SINGLE_VAULT.value),
            scheduled_interval_minutes=data.get("scheduled_interval_minutes", 10),
            debounce_seconds=data.get("debounce_seconds", 3.0),
            stability_check_count=data.get("stability_check_count", 3),
            compression_level=data.get("compression_level", "deflated"),
            retention=retention,
            use_unified_backup_dir=data.get("use_unified_backup_dir", False),
            unified_backup_directory=data.get("unified_backup_directory", str(Path.home() / "GameSaveGuardian_Backups")),
            start_with_windows=data.get("start_with_windows", False),
            minimize_to_tray=data.get("minimize_to_tray", True),
            theme=data.get("theme", "dark"),
            language=data.get("language", "en"),
            notifications_enabled=data.get("notifications_enabled", True),
            silent_gaming_mode=data.get("silent_gaming_mode", True),
            verbose_logging=data.get("verbose_logging", False),
            first_run_completed=data.get("first_run_completed", False),
        )
