"""Persistent configuration.

Settings are stored as one human-readable JSON document. Writes are atomic
(temporary file + rename) and every save keeps a rotating backup, because a
corrupted config would otherwise lose the user's whole wallpaper setup.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import paths
from .models import MonitorInfo, Playlist, Profile, ScheduleRule, TransitionKind

log = logging.getLogger(__name__)

MAX_BACKUPS = 10

DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    # --- General -----------------------------------------------------------
    "language": "system",            # "system" | "ru" | "en"
    "start_with_system": False,
    "minimize_to_tray": True,
    "close_to_tray": True,
    "check_updates": True,
    # --- Playback ----------------------------------------------------------
    "loop_videos": True,             # restart videos instead of ending
    "video_audio": False,            # wallpapers are silent by default
    "pause_on_fullscreen": True,
    "pause_on_lock": True,
    "pause_on_battery": False,
    "battery_pause_threshold": 20,   # percent
    "pause_on_remote_session": True,
    "pause_when_idle": False,
    "idle_minutes": 5,
    # --- Performance -------------------------------------------------------
    "memory_limit_mb": 1024,
    "max_fps": 30,
    "hardware_acceleration": True,   # web wallpapers
    "pause_when_other_app_fullscreen": True,
    "disable_battery_saver_live": True,
    # --- Library -----------------------------------------------------------
    "library_dirs": [],
    "watch_library": True,
    "thumb_size": 220,
    "remember_last_wallpaper": True,
    # --- Desktop integration ----------------------------------------------
    "accent_color": "auto",          # "auto" or "#rrggbb"
    "hide_desktop_icons": False,
    "screensaver_awareness": True,
    # --- Startup -----------------------------------------------------------
    "startup_profile_id": "",
    "last_profile_id": "",
    # --- State (not user-editable) ----------------------------------------
    "last_run_version": "",
    "first_run_done": False,
}


class Config:
    """Loads, exposes and saves the settings document.

    A change callback can be attached so the rest of the app reacts to edits
    without polling the file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.config_path()
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self._listeners: list[Callable[[str, Any], None]] = []
        self._dirty = False
        self.load()

    # ------------------------------------------------------------------ IO
    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> None:
        if not self._path.exists():
            self._data = dict(DEFAULT_SETTINGS)
            self.save()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Cannot read config %s: %s", self._path, exc)
            self._restore_backup()
            return
        if not isinstance(raw, dict):
            log.error("Config root is not an object; using defaults")
            self._data = dict(DEFAULT_SETTINGS)
            return
        merged = dict(DEFAULT_SETTINGS)
        merged.update(raw)
        self._data = merged

    def _restore_backup(self) -> None:
        backups = self._backup_files()
        if not backups:
            self._data = dict(DEFAULT_SETTINGS)
            return
        newest = backups[0]
        try:
            raw = json.loads(newest.read_text(encoding="utf-8"))
            log.warning("Recovered settings from backup %s", newest.name)
            self._data = {**DEFAULT_SETTINGS, **raw}
        except (json.JSONDecodeError, OSError):
            self._data = dict(DEFAULT_SETTINGS)

    def _backup_files(self) -> list[Path]:
        d = paths.config_backup_dir()
        if not d.exists():
            return []
        return sorted(d.glob("config-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._write_backup()
        payload = json.dumps(self._data, indent=2, ensure_ascii=False, sort_keys=False)
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".config-", suffix=".tmp"
        )
        try:
            with open(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.write("\n")
                fh.flush()
                try:
                    import os

                    os.fsync(fh.fileno())
                except OSError:
                    pass
            Path(tmp_name).replace(self._path)
        except OSError as exc:
            log.error("Failed to save config: %s", exc)
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
        self._dirty = False

    def _write_backup(self) -> None:
        try:
            d = paths.config_backup_dir()
            d.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self._path, d / f"config-{stamp}.json")
            old = sorted(d.glob("config-*.json"))
            for stale in old[MAX_BACKUPS:]:
                stale.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not write config backup: %s", exc)

    # ------------------------------------------------------------- accessors
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULT_SETTINGS.get(key, default))

    def set(self, key: str, value: Any, *, save: bool = True, notify: bool = True) -> None:
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._dirty = True
        if notify:
            for cb in list(self._listeners):
                try:
                    cb(key, value)
                except Exception:  # noqa: BLE001 - a bad listener must not break saving
                    log.exception("Config listener failed for key %r", key)
        if save:
            self.save()

    def update(self, values: dict[str, Any], *, save: bool = True) -> None:
        for key, value in values.items():
            self.set(key, value, save=False)
        if save:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def add_listener(self, callback: Callable[[str, Any], None]) -> None:
        self._listeners.append(callback)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def reset_defaults(self) -> None:
        self._data = dict(DEFAULT_SETTINGS)
        self.save()

    # ------------------------------------------------- structured sections
    # These live in separate files next to the settings document so a large
    # library of playlists never bloats the frequently rewritten config.

    def _section_path(self, name: str) -> Path:
        return self._path.parent / f"{name}.json"

    def _load_section(self, name: str) -> list[dict[str, Any]]:
        p = self._section_path(name)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Cannot read %s: %s", p, exc)
            return []
        return data if isinstance(data, list) else []

    def _save_section(self, name: str, items: list[dict[str, Any]]) -> None:
        p = self._section_path(name)
        try:
            p.write_text(
                json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            log.error("Cannot write %s: %s", p, exc)

    # -- profiles --
    def load_profiles(self) -> list[Profile]:
        return [Profile.from_dict(d) for d in self._load_section("profiles")]

    def save_profiles(self, profiles: list[Profile]) -> None:
        self._save_section("profiles", [p.to_dict() for p in profiles])

    # -- playlists --
    def load_playlists(self) -> list[Playlist]:
        return [Playlist.from_dict(d) for d in self._load_section("playlists")]

    def save_playlists(self, playlists: list[Playlist]) -> None:
        self._save_section("playlists", [p.to_dict() for p in playlists])

    # -- rules --
    def load_rules(self) -> list[ScheduleRule]:
        return [ScheduleRule.from_dict(d) for d in self._load_section("rules")]

    def save_rules(self, rules: list[ScheduleRule]) -> None:
        self._save_section("rules", [r.to_dict() for r in rules])

    # -- monitors --
    def load_monitors(self) -> list[MonitorInfo]:
        return [MonitorInfo.from_dict(d) for d in self._load_section("monitors")]

    def save_monitors(self, monitors: list[MonitorInfo]) -> None:
        self._save_section("monitors", [m.to_dict() for m in monitors])

    # ------------------------------------------------------------ shortcuts
    def default_transition(self) -> str:
        return TransitionKind.FADE.value


__all__ = ["Config", "DEFAULT_SETTINGS", "MAX_BACKUPS"]