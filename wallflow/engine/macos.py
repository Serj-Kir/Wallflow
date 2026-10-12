"""macOS wallpaper backend (a bonus target, kept simple).

macOS rotates through the pictures stored in a desktop picture folder, so the
integration writes an image there and refreshes the system database.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

from .. import utils
from ..models import MonitorInfo
from .base import (
    BackendCapabilities,
    WallpaperBackend,
    WallpaperRequest,
)

log = logging.getLogger(__name__)

_PICTURES = Path.home() / "Pictures" / "Wallflow"


class MacOSBackend(WallpaperBackend):
    name = "macos"
    label = "macOS"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=False, clear_to_color=True)
        _PICTURES.mkdir(parents=True, exist_ok=True)

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None:
            return False
        source = Path(request.path).resolve()
        if not source.exists():
            return False
        target = _PICTURES / source.name
        try:
            if source != target:
                shutil.copy2(source, target)
        except OSError as exc:
            log.debug("Copying wallpaper into the pictures folder failed: %s", exc)
            return False
        for argv in (
            ["/usr/bin/osascript", "-e", f'tell application "System Events" to set picture of every desktop to POSIX file "{target}"'],
            ["/System/Library/Automator/SetDesktopPicture.action/Contents/Resources/setdesktoppicture", str(target)],
        ):
            if Path(argv[0]).exists():
                proc = utils.run(argv)
                if proc.returncode == 0:
                    return True
        return False

    def clear(self) -> bool:
        return self.set_wallpaper(WallpaperRequest(path=None))

    def monitors(self) -> list[MonitorInfo]:
        return []


__all__ = ["MacOSBackend"]