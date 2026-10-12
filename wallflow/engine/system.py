"""Runtime system state used to decide when playback should pause."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from .. import utils

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:  # noqa: BLE001 - non-Windows import smoke tests
        _user32 = _kernel32 = None  # type: ignore[assignment]
else:
    _user32 = _kernel32 = None  # type: ignore[assignment]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("Reserved1", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_uint32),
        ("BatteryFullLifeTime", ctypes.c_uint32),
    ]


@dataclass
class PowerState:
    on_battery: bool = False
    percent: int = 100
    charging: bool = True


@dataclass
class DisplayState:
    fullscreen: bool = False
    locked: bool = False
    idle_seconds: float = 0.0


class SystemWatcher:
    """Polls the few OS facts Wallflow needs to pause playback.

    Polling rather than subscribing keeps the implementation portable: the same
    code path works under Wayland, X11 and Windows without extra dependencies.
    """

    def __init__(self, interval: float = 5.0) -> None:
        self._interval = interval
        self._last_check = 0.0
        self._power = PowerState()
        self._display = DisplayState()
        self._subscribers: list[Callable[[PowerState, DisplayState], None]] = []
        self._idle_warn_at = 0.0

    def subscribe(self, callback: Callable[[PowerState, DisplayState], None]) -> None:
        self._subscribers.append(callback)

    def poll(self, *, force: bool = False) -> tuple[PowerState, DisplayState]:
        now = time.monotonic()
        if not force and now - self._last_check < self._interval:
            return self._power, self._display
        self._last_check = now
        self._power = self._read_power()
        self._display = self._read_display()
        for cb in list(self._subscribers):
            try:
                cb(self._power, self._display)
            except Exception:  # noqa: BLE001
                log.exception("System watcher callback failed")
        return self._power, self._display

    # ---------------------------------------------------------------- power
    def _read_power(self) -> PowerState:
        if IS_WINDOWS and _kernel32 is not None:
            status = SYSTEM_POWER_STATUS()
            if _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                percent = int(status.BatteryLifePercent)
                if percent == 255:  # unknown (desktop / no battery)
                    percent = 100
                on_battery = status.ACLineStatus == 0
                return PowerState(on_battery=on_battery, percent=percent, charging=not on_battery)
            return PowerState()

        if IS_LINUX:
            path = "/sys/class/power_supply"
            mains = batteries: list[tuple[str, int, bool]] = []
            try:
                for entry in sorted(os.listdir(path)):
                    if not entry.startswith(("BAT", "ADP")):
                        continue
                    d = os.path.join(path, entry)
                    kind = _read_text(os.path.join(d, "type")) or ""
                    if kind == "Mains":
                        online = _read_text(os.path.join(d, "online")) == "1"
                        mains.append((entry, 100, online))
                        continue
                    raw = _read_text(os.path.join(d, "capacity"))
                    if raw and raw.strip().isdigit():
                        batteries.append((entry, int(raw.strip()), True))
            except OSError:
                pass
            if not batteries and not mains:
                return PowerState()
            # A desktop with only a mains adapter counts as fully charged.
            if not batteries:
                online = any(on for _, _, on in mains)
                return PowerState(on_battery=not online, percent=100, charging=online)
            entry, percent, _ = batteries[0]
            online = any(on for _, _, on in mains) or percent >= 100
            return PowerState(on_battery=not online, percent=percent, charging=online)

        return PowerState()

    # -------------------------------------------------------------- display
    def _read_display(self) -> DisplayState:
        state = DisplayState(idle_seconds=self._idle_seconds())
        if IS_WINDOWS and _user32 is not None:
            state.fullscreen = self._windows_fullscreen()
            state.locked = self._windows_locked()
        elif IS_LINUX:
            state.fullscreen = self._linux_fullscreen()
            state.locked = self._linux_locked()
        return state

    @staticmethod
    def _idle_seconds() -> float:
        """Seconds since the last user input, or 0 when it cannot be determined."""
        if IS_WINDOWS and _user32 is not None:
            return max(0.0, (_now_ms() - _last_input_ms()) / 1000.0)
        if IS_LINUX:
            proc = utils.run(["xprintidle"], timeout=3)
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                return float(proc.stdout.strip()) / 1000.0
        return 0.0

    def _windows_fullscreen(self) -> bool:
        from ctypes import wintypes

        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return False
            rect = wintypes.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rect))
            screen = wintypes.RECT()
            _user32.GetWindowRect(_user32.GetDesktopWindow(), ctypes.byref(screen))
            screen_w = screen.right - screen.left
            screen_h = screen.bottom - screen.top
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top
            # Within a couple of pixels of the desktop counts as fullscreen.
            return win_w >= screen_w - 2 and win_h >= screen_h - 2
        except Exception:  # noqa: BLE001
            return False

    def _windows_locked(self) -> bool:
        try:
            hwnd = _user32.GetForegroundWindow()
            return hwnd == _user32.GetDesktopWindow()
        except Exception:  # noqa: BLE001
            return False

    def _linux_fullscreen(self) -> bool:
        """Ask the compositor whether the active window is fullscreen."""
        if utils.which("swaymsg"):
            proc = utils.run(
                ["swaymsg", "-t", "get_tree"], timeout=4, check=False
            )
            if proc.returncode == 0:
                try:
                    import json

                    tree = json.loads(proc.stdout)
                    return _sway_tree_is_fullscreen(tree)
                except (json.JSONDecodeError, TypeError):
                    pass
        return False

    @staticmethod
    def _linux_locked() -> bool:
        # Under X11 a locked screen is signalled by the screensaver grabbing
        # the server; gsettings-based desktops simply keep the session alive so
        # we conservatively report "not locked".
        return False


def _sway_tree_is_fullscreen(tree: dict) -> bool:
    if tree.get("is_focused") and tree.get("fullscreen_mode") in (1, "1", True):
        return True
    for node in tree.get("nodes", []) or []:
        if isinstance(node, dict) and node.get("fullscreen_mode") in (1, "1", True) and node.get("is_focused"):
            return True
    return False


def _now_ms() -> int:  # pragma: no cover - Windows path
    return int(time.time() * 1000)


def _last_input_ms() -> int:  # pragma: no cover - Windows path
    """Milliseconds of idle time, via GetLastInputInfo."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    try:
        if _user32 and _user32.GetLastInputInfo(ctypes.byref(info)):
            return int(info.dwTime)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.readline().strip()
    except OSError:
        return default


__all__ = ["SystemWatcher", "PowerState", "DisplayState", "SYSTEM_POWER_STATUS"]