"""Start Wallflow together with the system session."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from . import utils

log = logging.getLogger(__name__)

DESKTOP_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Wallflow
Comment={comment}
Exec={exec_line}
Icon={icon}
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
"""


def _entry_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/Startup/wallflow.cmd"
    return Path.home() / ".config" / "autostart" / "wallflow.desktop"


def launch_command() -> str:
    """The command that starts this installation."""
    from . import paths

    if paths.is_frozen():
        return f'"{Path(sys.executable).resolve()}"'
    launcher = paths.resource_root() / "run.py"
    return f'"{Path(sys.executable).resolve()}" "{launcher}"'


def enable() -> bool:
    path = _entry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            path.write_text(
                f'@echo off\r\nstart "" {launch_command()}\r\n', encoding="utf-8"
            )
        else:
            path.write_text(
                DESKTOP_ENTRY.format(
                    comment="Live wallpaper engine",
                    exec_line=launch_command(),
                    icon="wallflow",
                ),
                encoding="utf-8",
            )
        path.chmod(0o644)
    except OSError as exc:
        log.error("Could not enable autostart: %s", exc)
        return False
    log.info("Autostart enabled: %s", path)
    return True


def disable() -> bool:
    path = _entry_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.error("Could not disable autostart: %s", exc)
        return False
    log.info("Autostart disabled")
    return True


def is_enabled() -> bool:
    return _entry_path().exists()


def sync(enabled: bool) -> None:
    """Make the system match ``enabled`` without bothering the user."""
    try:
        if enabled and not is_enabled():
            enable()
        elif not enabled and is_enabled():
            disable()
    except Exception:  # noqa: BLE001 - autostart is a convenience, not a feature
        log.debug("Autostart sync failed", exc_info=True)


__all__ = ["enable", "disable", "is_enabled", "sync", "launch_command"]