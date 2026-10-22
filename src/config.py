"""
Persistent configuration management for Game Save Guardian.
Uses atomic JSON writes and versioned schema defaults.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from src.models import AppSettings, GameConfig, RetentionPolicy
from src.utils.logging_config import get_logger
from src.utils.paths import get_config_path

CONFIG_VERSION = 1


class ConfigManager:
    """Manages application settings persistence with atomic writes."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or get_config_path()
        self.logger = get_logger()
        self._settings: Optional[AppSettings] = None

    def get_settings(self) -> AppSettings:
        """Get the current settings, loading from disk if not yet loaded."""
        if self._settings is None:
            self._settings = self.load_config()
        return self._settings

    def load_config(self) -> AppSettings:
        """Load settings from JSON file or return defaults if file is missing/invalid."""
        if not self.config_path.exists():
            self.logger.info("Config file not found at %s. Initializing default settings.", self.config_path)
            settings = AppSettings()
            self._settings = settings
            self.save_config(settings)
            return settings

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check config version for future migrations
            file_version = data.get("config_version", 1)
            raw_settings = data.get("settings", {})
            settings = AppSettings.from_dict(raw_settings)
            self._settings = settings
            self.logger.info("Configuration loaded from %s (v%d)", self.config_path, file_version)
            return settings
        except Exception as e:
            self.logger.error("Failed to parse config file (%s). Falling back to defaults. Error: %s", self.config_path, e)
            settings = AppSettings()
            self._settings = settings
            return settings

    def save_config(self, settings: Optional[AppSettings] = None) -> bool:
        """
        Atomically write settings to disk using a temporary file and rename.
        Prevents corrupt config files during sudden power loss or process kill.
        """
        if settings is not None:
            self._settings = settings
        elif self._settings is None:
            self._settings = AppSettings()

        target_dir = self.config_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        temp_path = self.config_path.with_suffix(".tmp")

        payload = {
            "config_version": CONFIG_VERSION,
            "settings": self._settings.to_dict()
        }

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            temp_path.replace(self.config_path)
            self.logger.debug("Configuration atomically saved to %s", self.config_path)
            return True
        except Exception as e:
            self.logger.error("Failed to save config to %s: %s", self.config_path, e)
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False

    def update_settings(self, **kwargs) -> AppSettings:
        """Update specific settings attributes and persist immediately."""
        settings = self.get_settings()
        for k, v in kwargs.items():
            if hasattr(settings, k):
                setattr(settings, k, v)
        self.save_config(settings)
        return settings
