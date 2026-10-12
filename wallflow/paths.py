"""Filesystem locations used by Wallflow.

Everything the app writes lives under a single per-user data directory so the
app stays portable and uninstalls cleanly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIRNAME = "wallflow"

#: Sub-directories created inside the data directory.
SUBDIRS = ("wallpapers", "covers", "thumbs", "logs", "backups")


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """Root of bundled (read-only) resources."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def _xdg(env: str, fallback: str) -> Path:
    value = os.environ.get(env, "").strip()
    if not value or not Path(value).is_absolute():
        value = os.path.join(Path.home().as_posix(), fallback)
    return Path(value)


def data_dir() -> Path:
    """Writable directory holding config, library and logs.

    Honours ``WALLFLOW_DATA_DIR`` which is handy for portable installs and for
    running a second isolated instance during testing.
    """
    override = os.environ.get("WALLFLOW_DATA_DIR", "").strip()
    if override:
        root = Path(override)
    elif sys.platform == "win32":
        root = _xdg("APPDATA", "AppData/Roaming") / APP_DIRNAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_DIRNAME
    else:
        root = _xdg("XDG_DATA_HOME", ".local/share") / APP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return data_dir() / "config.json"


def config_backup_dir() -> Path:
    return data_dir() / "backups"


def wallpaper_dir() -> Path:
    return data_dir() / "wallpapers"


def cover_dir() -> Path:
    return data_dir() / "covers"


def thumb_dir() -> Path:
    return data_dir() / "thumbs"


def log_dir() -> Path:
    return data_dir() / "logs"


def cache_dir() -> Path:
    """Scratch space that may be wiped at any time."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_DIRNAME / "Cache"
    else:
        root = _xdg("XDG_CACHE_HOME", ".cache") / APP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_dirs() -> None:
    for name in SUBDIRS:
        (data_dir() / name).mkdir(parents=True, exist_ok=True)


__all__ = [
    "APP_DIRNAME",
    "SUBDIRS",
    "is_frozen",
    "resource_root",
    "data_dir",
    "config_path",
    "config_backup_dir",
    "wallpaper_dir",
    "cover_dir",
    "thumb_dir",
    "log_dir",
    "cache_dir",
    "ensure_dirs",
]