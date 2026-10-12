"""Backend interface for setting the desktop wallpaper.

A backend translates a Wallflow request ("show image X on monitor Y with fill")
into whatever the running operating system understands. Concrete backends live
in :mod:`wallflow.engine.linux` and :mod:`wallflow.engine.windows`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Sequence

from ..models import MonitorInfo

log = logging.getLogger(__name__)


class FitMode(str, Enum):
    FILL = "fill"
    FIT = "fit"
    STRETCH = "stretch"
    CENTER = "center"
    TILE = "tile"


@dataclass
class WallpaperRequest:
    """Everything a backend needs to set one wallpaper on one monitor."""

    path: Path | None
    monitor_id: str = ""
    fit: FitMode = FitMode.FILL
    #: Kept for backends that support distinct light/dark variants.
    dark_path: Path | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class BackendCapabilities:
    """Which optional features the active backend supports."""

    per_monitor: bool = False
    per_monitor_slideshow: bool = False
    fit_modes: tuple[str, ...] = ("fill", "fit", "stretch", "center", "tile")
    video_native: bool = False     # backend renders video by itself
    accent_color: bool = False     # can tint the desktop/shell to match
    clear_to_color: bool = False


class WallpaperBackend:
    """Base class. Subclasses must implement :meth:`set_wallpaper`."""

    name: str = "none"
    label: str = "Unsupported"

    def __init__(self) -> None:
        self.capabilities = BackendCapabilities()

    # ------------------------------------------------------------ interface
    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        raise NotImplementedError

    def clear(self) -> bool:
        raise NotImplementedError

    def monitors(self) -> list[MonitorInfo]:
        return []

    def refresh(self) -> None:
        """Re-read the desktop environment's state (monitors, theming)."""

    def set_accent_color(self, hex_color: str) -> bool:
        return False

    # -------------------------------------------------------------- helpers
    @staticmethod
    def normalise_hex(color: str) -> str:
        """Convert ``#rgb`` / ``rrggbb`` / ``#rrggbbbb`` into ``#rrggbb``."""
        c = color.strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) == 8:
            c = c[:6]
        if len(c) != 6:
            return "#000000"
        try:
            int(c, 16)
        except ValueError:
            return "#000000"
        return "#" + c.lower()

    @staticmethod
    def to_rgb_int(color: str) -> int:
        c = WallpaperBackend.normalise_hex(color)[1:]
        r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
        # Windows expects 0x00BBGGRR for colourref values.
        return (b << 16) | (g << 8) | r

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<{type(self).__name__} name={self.name!r}>"


@dataclass
class BackendStack:
    """The chosen backend plus the fallbacks to try if it fails.

    Some desktops need a primary method and a fallback (e.g. KDE works through
    its plasma scripting API but a plain file copy is a reasonable safety net).
    """

    primary: WallpaperBackend
    fallbacks: list[WallpaperBackend] = field(default_factory=list)

    @property
    def capabilities(self) -> BackendCapabilities:
        return self.primary.capabilities

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        backends: Sequence[WallpaperBackend] = [self.primary, *self.fallbacks]
        for backend in backends:
            try:
                if backend.set_wallpaper(request):
                    return True
            except Exception:  # noqa: BLE001 - one backend failing is not fatal
                log.exception("Backend %s failed for %s", backend.name, request.path)
        log.error("All backends failed to set wallpaper %s", request.path)
        return False

    def clear(self) -> bool:
        ok = False
        for backend in [self.primary, *self.fallbacks]:
            try:
                ok = backend.clear() or ok
            except Exception:  # noqa: BLE001
                log.exception("Backend %s failed to clear", backend.name)
        return ok


__all__ = [
    "FitMode",
    "WallpaperRequest",
    "BackendCapabilities",
    "WallpaperBackend",
    "BackendStack",
]