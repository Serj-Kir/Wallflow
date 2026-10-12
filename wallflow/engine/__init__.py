"""Backend selection and monitor discovery."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from ..models import MonitorInfo
from .base import (
    BackendCapabilities,
    BackendStack,
    FitMode,
    WallpaperBackend,
    WallpaperRequest,
)

log = logging.getLogger(__name__)


class NullBackend(WallpaperBackend):
    """Used when no desktop integration could be detected."""

    name = "null"
    label = "None"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities()

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        log.info(
            "No desktop integration available; wallpaper %s was not applied. "
            "Animated wallpapers still render through Wallflow's own window.",
            request.path,
        )
        return False

    def clear(self) -> bool:
        return False


def create_stack() -> BackendStack:
    """Pick the best backend chain for the current platform."""
    if sys.platform == "win32":
        try:
            from .windows import WindowsBackend

            primary = WindowsBackend()
            return BackendStack(primary=primary, fallbacks=[NullBackend()])
        except Exception:  # noqa: BLE001
            log.exception("Could not initialise the Windows backend")
            return BackendStack(primary=NullBackend())

    if sys.platform.startswith("linux"):
        from .linux import detect_backends

        backends = detect_backends()
        if backends:
            return BackendStack(primary=backends[0], fallbacks=backends[1:] + [NullBackend()])
        return BackendStack(primary=NullBackend())

    if sys.platform == "darwin":
        from .macos import MacOSBackend

        primary = MacOSBackend()
        return BackendStack(primary=primary, fallbacks=[NullBackend()])

    return BackendStack(primary=NullBackend())


def monitors_from_qt(screens) -> list[MonitorInfo]:
    """Build monitor records from ``QGuiApplication.screens()``.

    Qt's ``name()`` matches the compositor output name on Wayland (e.g. ``DP-1``)
    and on X11 it matches ``xdpyname``, which is close enough to correlate with
    the names the Linux backends report.
    """
    out: list[MonitorInfo] = []
    for index, screen in enumerate(screens):
        geometry = screen.geometry()
        dpr = float(screen.devicePixelRatio() or 1.0)
        name = screen.name() or f"monitor-{index}"
        out.append(
            MonitorInfo(
                id=name,
                name=name,
                width=int(geometry.width() * dpr),
                height=int(geometry.height() * dpr),
                x=int(geometry.x() * dpr),
                y=int(geometry.y() * dpr),
                scale=dpr,
                primary=(index == 0 or geometry.x() == 0 and geometry.y() == 0),
                identifier=name,
            )
        )
    return out


def merge_monitors(
    qt_monitors: list[MonitorInfo], backend_monitors: list[MonitorInfo]
) -> list[MonitorInfo]:
    """Combine Qt geometry with compositor-reported ids.

    Qt gives reliable geometry and DPI; the backend gives the names the
    wallpaper commands need. Match them up positionally, falling back to Qt's
    own naming when the backend reports nothing useful.
    """
    if not backend_monitors:
        return qt_monitors
    if not qt_monitors:
        return backend_monitors

    merged: list[MonitorInfo] = []
    used: set[str] = set()
    for index, qt_mon in enumerate(qt_monitors):
        match = _best_match(qt_mon, backend_monitors, used)
        if match is None and index < len(backend_monitors):
            candidate = backend_monitors[index]
            if candidate.id not in used:
                match = candidate
                used.add(candidate.id)
        if match is None:
            merged.append(qt_mon)
        else:
            merged.append(
                MonitorInfo(
                    id=match.id,
                    name=match.name or qt_mon.name,
                    width=qt_mon.width or match.width,
                    height=qt_mon.height or match.height,
                    x=qt_mon.x,
                    y=qt_mon.y,
                    scale=qt_mon.scale,
                    primary=qt_mon.primary or match.primary,
                    identifier=match.id,
                )
            )
    # Any backend monitors we never matched (e.g. a disconnected output) are
    # dropped: Qt only reports physical screens that actually exist.
    return merged


def _best_match(
    qt_mon: MonitorInfo, candidates: list[MonitorInfo], used: set[str]
) -> Optional[MonitorInfo]:
    if qt_mon.id in {c.id for c in candidates}:
        for candidate in candidates:
            if candidate.id == qt_mon.id and candidate.id not in used:
                used.add(candidate.id)
                return candidate
    for candidate in candidates:
        if candidate.id in used:
            continue
        if candidate.width == qt_mon.width and candidate.height == qt_mon.height:
            used.add(candidate.id)
            return candidate
    return None


__all__ = [
    "NullBackend",
    "create_stack",
    "monitors_from_qt",
    "merge_monitors",
    "FitMode",
    "WallpaperRequest",
    "BackendStack",
]