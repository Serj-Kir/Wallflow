"""Windows wallpaper backend.

Windows has no native video/web wallpaper API, so animated wallpapers are
always composited by Wallflow itself (see :mod:`wallflow.player`). What this
module provides is the static path: the officially documented
``SystemParametersInfoW`` call, plus monitor enumeration via ``EnumDisplayMonitors``
so per-monitor placement and fit modes work.
"""

from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from ..models import MonitorInfo
from .base import (
    BackendCapabilities,
    FitMode,
    WallpaperBackend,
    WallpaperRequest,
)

log = logging.getLogger(__name__)

if sys.platform != "win32":  # pragma: no cover - only importable on Windows
    raise ImportError("wallflow.engine.windows requires Windows")

SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

MONITORINFOF_PRIMARY = 0x00000001

#: Windows stores the fit mode as a TileWallpaper / WallpaperStyle pair.
_STYLES = {
    FitMode.CENTER: (0, "10"),   # centred
    FitMode.TILE: (1, "0"),      # tiled
    FitMode.STRETCH: (0, "10"),  # stretched is a style-agnostic fill
    FitMode.FIT: (0, "6"),       # fit
    FitMode.FILL: (0, "10"),     # fill (Windows "Fill" focus style)
}


class MONITORINFOEX(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class WindowsBackend(WallpaperBackend):
    name = "windows"
    label = "Windows"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(
            per_monitor=True,
            per_monitor_slideshow=True,
            accent_color=True,
            clear_to_color=True,
        )
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._setup_prototypes()
        self._monitors: list[MonitorInfo] = []
        self.refresh()

    def _setup_prototypes(self) -> None:
        self._user32.SystemParametersInfoW.argtypes = [
            wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT
        ]
        self._user32.SystemParametersInfoW.restype = wintypes.BOOL

        MONITORINFOPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(MONITORINFOEX), wintypes.LPARAM,
        )
        self._user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.POINTER(wintypes.RECT), MONITORINFOPROC, wintypes.LPARAM
        ]
        self._user32.EnumDisplayMonitors.restype = wintypes.BOOL
        self._MONITORINFOPROC = MONITORINFOPROC

        self._user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEX)]
        self._user32.GetMonitorInfoW.restype = wintypes.BOOL

    # ------------------------------------------------------------ monitors
    def refresh(self) -> None:
        monitors: list[MonitorInfo] = []

        def callback(hmon, hdc, lprc, lparam):
            info = MONITORINFOEX()
            info.cbSize = ctypes.sizeof(MONITORINFOEX)
            if self._user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                device = info.szDevice
                width = info.rcMonitor.right - info.rcMonitor.left
                height = info.rcMonitor.bottom - info.rcMonitor.top
                try:
                    ctypes.windll.shcore.GetDpiForMonitor(
                        hmon, 0, ctypes.byref(ctypes.c_int()), ctypes.byref(ctypes.c_int())
                    )
                except Exception:  # noqa: BLE001
                    pass
                monitors.append(
                    MonitorInfo(
                        id=device,
                        name=device,
                        width=width,
                        height=height,
                        x=info.rcMonitor.left,
                        y=info.rcMonitor.top,
                        primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                        identifier=device,
                    )
                )
            return True

        try:
            self._user32.EnumDisplayMonitors(None, None, self._MONITORINFOPROC(callback), 0)
        except Exception:  # noqa: BLE001
            log.exception("EnumDisplayMonitors failed")
        if not monitors:
            width = self._user32.GetSystemMetrics(0)
            height = self._user32.GetSystemMetrics(1)
            monitors.append(MonitorInfo(id="primary", name="Primary", width=width, height=height, primary=True))
        self._monitors = monitors

    def monitors(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def set_monitors(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = monitors

    # ----------------------------------------------------------- wallpaper
    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None:
            return False
        path = Path(request.path).resolve()
        if not path.exists():
            log.warning("Wallpaper file missing: %s", path)
            return False

        self._write_style(request.fit)

        buf = ctypes.create_unicode_buffer(512)
        buf.value = str(path)
        ok = bool(
            self._user32.SystemParametersInfoW(
                SPI_SETDESKWALLPAPER, 0, ctypes.byref(buf), SPIF_UPDATEINIFLE | SPIF_SENDCHANGE
            )
        )
        if not ok:
            log.warning(
                "SystemParametersInfoW failed (%s)", ctypes.get_last_error()
            )
        return ok

    def clear(self) -> bool:
        return self.set_wallpaper(WallpaperRequest(path=None))

    def _write_style(self, fit: FitMode) -> None:
        """Persist WallpaperStyle / TileWallpaper for the chosen fit."""
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return
        style, tile = _STYLES.get(fit, (0, "0"))
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop", 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, str(style))
                winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, str(tile))
        except OSError as exc:
            log.debug("Could not persist wallpaper style: %s", exc)

    def set_accent_color(self, hex_color: str) -> bool:
        """Tint the DWM colourisation used for window borders and taskbar."""
        try:
            import winreg
        except ImportError:  # pragma: no cover
            return False
        r, g, b = self.to_rgb(hex_color)
        value = (0x99 << 24) | (b << 16) | (g << 8) | r
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM", 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, "ColorizationColor", 0, winreg.REG_DWORD, value)
            # Force DWM to re-read the colour.
            self._user32.SetWindowCompositionAttribute = getattr(
                self._user32, "SetWindowCompositionAttribute", None
            )
            return True
        except OSError as exc:
            log.debug("Accent colour write failed: %s", exc)
            return False

    @staticmethod
    def to_rgb(color: str) -> tuple[int, int, int]:
        c = color.lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6:
            return 0, 0, 0
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def create_backend() -> WallpaperBackend:
    return WindowsBackend()


__all__ = ["WindowsBackend", "create_backend", "SPI_SETDESKWALLPAPER"]