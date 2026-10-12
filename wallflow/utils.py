"""Small helpers shared across the app."""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

RUN_KWARGS: dict = (
    {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    if IS_WINDOWS
    else {}
)


def setup_logging(level: str = "INFO") -> None:
    """Configure logging to both stderr and a rotating file."""
    from . import paths

    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "wallflow.log"
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(fmt))
    root.addHandler(stream)
    try:
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
    except OSError:
        pass


def which(program: str) -> str | None:
    return shutil.which(program)


def run(
    argv: Sequence[str],
    *,
    timeout: float = 15.0,
    check: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a command without flashing a console window on Windows."""
    full_env = None
    if env:
        full_env = {**os.environ, **env}
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            env=full_env,
            **RUN_KWARGS,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(list(argv), 127, "", "not found")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(list(argv), 124, "", "timeout")
    except OSError as exc:  # pragma: no cover - defensive
        return subprocess.CompletedProcess(list(argv), 1, "", str(exc))


def desktop_environment() -> str:
    """Best-effort detection of the running Linux desktop."""
    if not IS_LINUX:
        return ""
    xdg = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    if xdg:
        # "ubuntu:GNOME" -> prefer the part after the colon.
        parts = [p.strip() for p in xdg.replace(":", ";").split(";") if p.strip()]
        for candidate in ("kde", "plasma", "gnome", "sway", "hyprland", "xfce", "cinnamon", "mate", "lxqt", "budgie"):
            if any(candidate in p for p in parts):
                return candidate
        return parts[-1] if parts else ""
    if os.environ.get("SWAYSOCK"):
        return "sway"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    return "unknown"


def session_type() -> str:
    """Return 'wayland', 'x11' or 'headless'."""
    if IS_MACOS or IS_WINDOWS:
        return "x11"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "headless"


def human_size(num_bytes: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < step:
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= step
    return f"{num_bytes:.1f} PB"


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_filename(name: str, fallback: str = "wallpaper") -> str:
    """Strip characters that are illegal on Windows or confusing elsewhere."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    if not cleaned or cleaned.upper() in reserved:
        return fallback
    return cleaned[:120]


def unique_path(path: Path) -> Path:
    """Return `path`, or `name-1.ext`, `name-2.ext`... if it already exists."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 1000):
        candidate = parent / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem}-{int(os.urandom(4).hex())}{suffix}"


def iter_files(root: Path, suffixes: Iterable[str], *, recursive: bool = True) -> list[Path]:
    exts = {s.lower() for s in suffixes}
    pattern = "**/*" if recursive else "*"
    found: list[Path] = []
    try:
        for entry in root.glob(pattern):
            if not entry.is_file():
                continue
            if entry.suffix.lower() in exts and not entry.name.startswith("."):
                found.append(entry)
    except OSError:
        return []
    return found


def truncate_middle(text: str, limit: int = 48) -> str:
    if len(text) <= limit:
        return text
    head = (limit - 1) // 2
    tail = limit - head - 1
    return f"{text[:head]}…{text[-tail:]}"


def format_resolution(width: int, height: int) -> str:
    known = {
        (1920, 1080): "1080p", (2560, 1440): "1440p", (3840, 2160): "4K",
        (7680, 4320): "8K", (1366, 768): "1366x768", (1920, 1200): "1920x1200",
        (2560, 1600): "2560x1600", (3440, 1440): "3440x1440", (1280, 1024): "1280x1024",
    }
    return known.get((width, height), f"{width}x{height}")


def platform_label() -> str:
    return {
        "linux": "Linux",
        "win32": "Windows",
        "darwin": "macOS",
    }.get(sys.platform, platform.system() or "?")


__all__ = [
    "IS_WINDOWS", "IS_LINUX", "IS_MACOS",
    "setup_logging", "which", "run", "desktop_environment", "session_type",
    "human_size", "human_duration", "clamp", "safe_filename", "unique_path",
    "iter_files", "truncate_middle", "format_resolution", "platform_label",
]