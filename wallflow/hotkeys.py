"""Global hotkeys.

Windows uses ``RegisterHotKey``. Under X11 the key is grabbed directly on the
root window. On Wayland, where clients are not allowed to grab keys, hotkeys
degrade to application-level shortcuts that only fire while the window has
focus - and the UI says so rather than pretending otherwise.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence

log = logging.getLogger(__name__)

#: action label -> default key sequence
DEFAULT_HOTKEYS: dict[str, str] = {
    "Пауза / воспроизведение": "Ctrl+Alt+P",
    "Следующие обои": "Ctrl+Alt+Right",
    "Предыдущие обои": "Ctrl+Alt+Left",
    "Открыть Wallflow": "Ctrl+Alt+W",
    "Показать панель управления": "",
}

# Windows virtual key codes for the modifier bits.
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# X11 keycode masks.
_X11_MOD_SHIFT = 1 << 0
_X11_MOD_CONTROL = 1 << 2
_X11_MOD_ALT = 1 << 3
_X11_MOD_SUPER = 1 << 6

# Qt -> X11 keysym mapping for the keys we allow.
_X11_KEYSYMS = {
    Qt.Key_A: 0x0061, Qt.Key_B: 0x0062, Qt.Key_C: 0x0063, Qt.Key_D: 0x0064,
    Qt.Key_E: 0x0065, Qt.Key_F: 0x0066, Qt.Key_G: 0x0067, Qt.Key_H: 0x0068,
    Qt.Key_I: 0x0069, Qt.Key_J: 0x006A, Qt.Key_K: 0x006B, Qt.Key_L: 0x006C,
    Qt.Key_M: 0x006D, Qt.Key_N: 0x006E, Qt.Key_O: 0x006F, Qt.Key_P: 0x0070,
    Qt.Key_Q: 0x0071, Qt.Key_R: 0x0072, Qt.Key_S: 0x0073, Qt.Key_T: 0x0074,
    Qt.Key_U: 0x0075, Qt.Key_V: 0x0076, Qt.Key_W: 0x0077, Qt.Key_X: 0x0078,
    Qt.Key_Y: 0x0079, Qt.Key_Z: 0x007A,
    Qt.Key_F1: 0xFFBE, Qt.Key_F2: 0xFFBF, Qt.Key_F3: 0xFFC0, Qt.Key_F4: 0xFFC1,
    Qt.Key_F5: 0xFFC2, Qt.Key_F6: 0xFFC3, Qt.Key_F7: 0xFFC4, Qt.Key_F8: 0xFFC5,
    Qt.Key_F9: 0xFFC6, Qt.Key_F10: 0xFFC7, Qt.Key_F11: 0xFFC8, Qt.Key_F12: 0xFFC9,
    Qt.Key_Left: 0xFF51, Qt.Key_Up: 0xFF52, Qt.Key_Right: 0xFF53, Qt.Key_Down: 0xFF54,
    Qt.Key_Space: 0x0020, Qt.Key_Tab: 0xFF09, Qt.Key_Escape: 0xFF1B,
    Qt.Key_Pause: 0xFF13, Qt.Key_Print: 0xFF61, Qt.Key_Home: 0xFF50, Qt.Key_End: 0xFF57,
    Qt.Key_PageUp: 0xFF55, Qt.Key_PageDown: 0xFF56,
}

# Qt -> Windows virtual key codes.
_WIN_VK = {
    Qt.Key_A: 0x41, Qt.Key_B: 0x42, Qt.Key_C: 0x43, Qt.Key_D: 0x44, Qt.Key_E: 0x45,
    Qt.Key_F: 0x46, Qt.Key_G: 0x47, Qt.Key_H: 0x48, Qt.Key_I: 0x49, Qt.Key_J: 0x4A,
    Qt.Key_K: 0x4B, Qt.Key_L: 0x4C, Qt.Key_M: 0x4D, Qt.Key_N: 0x4E, Qt.Key_O: 0x4F,
    Qt.Key_P: 0x50, Qt.Key_Q: 0x51, Qt.Key_R: 0x52, Qt.Key_S: 0x53, Qt.Key_T: 0x54,
    Qt.Key_U: 0x55, Qt.Key_V: 0x56, Qt.Key_W: 0x57, Qt.Key_X: 0x58, Qt.Key_Y: 0x59,
    Qt.Key_Z: 0x5A,
    Qt.Key_F1: 0x70, Qt.Key_F2: 0x71, Qt.Key_F3: 0x72, Qt.Key_F4: 0x73,
    Qt.Key_F5: 0x74, Qt.Key_F6: 0x75, Qt.Key_F7: 0x76, Qt.Key_F8: 0x77,
    Qt.Key_F9: 0x78, Qt.Key_F10: 0x79, Qt.Key_F11: 0x7A, Qt.Key_F12: 0x7B,
    Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    Qt.Key_Space: 0x20, Qt.Key_Tab: 0x09, Qt.Key_Escape: 0x1B, Qt.Key_Pause: 0x13,
    Qt.Key_Home: 0x24, Qt.Key_End: 0x23, Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
}


@dataclass
class Hotkey:
    action: str
    sequence: str
    callback: Callable[[], None]
    native_id: int = 0


def global_hotkeys_supported() -> tuple[bool, str]:
    """Whether real system-wide hotkeys can be registered here."""
    if sys.platform == "win32":
        return True, "Windows"
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        return False, "Wayland не даёт приложениям перехватывать клавиши глобально"
    if os.environ.get("DISPLAY"):
        return True, "X11"
    return False, "Неизвестная сессия дисплея"


class HotkeyManager(QAbstractNativeEventFilter):
    """Registers the configured hotkeys with the platform."""

    #: Signature of ``XErrorHandler``.
    _X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def __init__(self, parent=None) -> None:
        super().__init__()
        self._hotkeys: list[Hotkey] = []
        self._win_id = 0
        self._x11 = None
        self._x11_display = None
        self._x_error_handler = None
        self._failed: set[str] = set()

    # -------------------------------------------------------------- parsing
    @staticmethod
    def _parse(sequence: str) -> tuple[Qt.KeyboardModifiers, int]:
        parts = [p.strip().lower() for p in (sequence or "").split("+") if p.strip()]
        key = Qt.Key_unknown
        for part in parts:
            if part in ("ctrl", "control"):
                continue
            key = _KEY_NAMES.get(part, Qt.Key_unknown)
        modifiers = Qt.NoModifier
        for part in parts:
            if part in ("ctrl", "control"):
                modifiers |= Qt.ControlModifier
            elif part in ("alt",):
                modifiers |= Qt.AltModifier
            elif part in ("shift",):
                modifiers |= Qt.ShiftModifier
            elif part in ("meta", "super", "win"):
                modifiers |= Qt.MetaModifier
        return modifiers, int(key)

    # ----------------------------------------------------------- public api
    def register(self, bindings: dict[str, tuple[str, Callable[[], None]]], win_id: int = 0) -> None:
        """(Re)register all bindings. ``win_id`` is required on Windows."""
        self.unregister()
        self._win_id = win_id
        for index, (action, (sequence, callback)) in enumerate(bindings.items(), start=1):
            if not sequence:
                continue
            hotkey = Hotkey(action=action, sequence=sequence, callback=callback, native_id=index)
            ok = self._register_native(hotkey)
            if ok:
                self._hotkeys.append(hotkey)
            else:
                self._failed.add(action)
                log.warning("Could not register global hotkey %s (%s)", action, sequence)
        if self._failed:
            log.info("Unavailable hotkeys: %s", ", ".join(sorted(self._failed)))

    @property
    def failed(self) -> set[str]:
        return set(self._failed)

    @property
    def active(self) -> list[Hotkey]:
        return list(self._hotkeys)

    def unregister(self) -> None:
        if sys.platform == "win32":
            for hotkey in self._hotkeys:
                try:
                    ctypes.WinDLL("user32").UnregisterHotKey(None, hotkey.native_id)
                except Exception:  # noqa: BLE001
                    pass
        self._hotkeys.clear()

    # ------------------------------------------------------------ platform
    def _register_native(self, hotkey: Hotkey) -> bool:
        if sys.platform == "win32":
            return self._register_windows(hotkey)
        if os.environ.get("DISPLAY"):
            return self._register_x11(hotkey)
        return False

    def _register_windows(self, hotkey: Hotkey) -> bool:
        modifiers, qt_key = self._parse(hotkey.sequence)
        vk = _WIN_VK.get(qt_key)
        if vk is None or not self._win_id:
            return False
        flags = 0
        if modifiers & Qt.ControlModifier:
            flags |= _MOD_CONTROL
        if modifiers & Qt.AltModifier:
            flags |= _MOD_ALT
        if modifiers & Qt.ShiftModifier:
            flags |= _MOD_SHIFT
        if modifiers & Qt.MetaModifier:
            flags |= _MOD_WIN
        flags |= _MOD_NOREPEAT
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            if not user32.RegisterHotKey(None, hotkey.native_id, flags, vk):
                return False
        except Exception:  # noqa: BLE001
            log.debug("RegisterHotKey failed", exc_info=True)
            return False
        return True

    def _register_x11(self, hotkey: Hotkey) -> bool:
        display_ok = self._ensure_x11()
        if not display_ok:
            return False
        modifiers, qt_key = self._parse(hotkey.sequence)
        keysym = _X11_KEYSYMS.get(qt_key)
        if keysym is None:
            return False
        mask = 0
        if modifiers & Qt.ControlModifier:
            mask |= _X11_MOD_CONTROL
        if modifiers & Qt.AltModifier:
            mask |= _X11_MOD_ALT
        if modifiers & Qt.ShiftModifier:
            mask |= _X11_MOD_SHIFT
        if modifiers & Qt.MetaModifier:
            mask |= _X11_MOD_SUPER
        try:
            keycode = self._x11.XKeysymToKeycode(self._x11_display, ctypes.c_ulong(keysym))
            if not keycode:
                return False
            window = self._x11.XDefaultRootWindow(self._x11_display)
            self._x11.XGrabKey(
                self._x11_display, ctypes.c_ulong(keycode), ctypes.c_uint(mask), window, True
            )
            self._x11.XSync(self._x11_display, False)
        except Exception:  # noqa: BLE001
            log.debug("XGrabKey failed", exc_info=True)
            return False
        hotkey.native_id = keycode | (mask << 16)
        return True

    def _ensure_x11(self) -> bool:
        if self._x11 is not None:
            return self._x11_display is not None
        try:
            x11 = ctypes.CDLL("libX11.so.6")
        except OSError:
            return False

        # Xlib's default error handler calls exit(). A key that is already
        # grabbed by another application is completely normal here, so errors
        # are swallowed and reported through XGetErrorText instead.
        try:
            x11.XSetErrorHandler.argtypes = [self._X_ERROR_HANDLER]
            x11.XSetErrorHandler.restype = ctypes.c_void_p
            self._x_error_handler = self._X_ERROR_HANDLER(
                lambda display, event: 0  # 0 = "keep going"
            )
            x11.XSetErrorHandler(self._x_error_handler)
        except Exception:  # noqa: BLE001
            log.debug("Could not install an X11 error handler", exc_info=True)

        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        display = x11.XOpenDisplay(None)
        if not display:
            return False
        x11.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self._x11 = x11
        self._x11_display = display
        return True

    # --------------------------------------------------------- event filter
    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if sys.platform == "win32":
            try:
                import ctypes.wintypes

                msg = ctypes.wintypes.MSG.from_address(int(message))
            except Exception:  # noqa: BLE001
                return False, 0
            if msg.message == WM_HOTKEY:
                for hotkey in self._hotkeys:
                    if hotkey.native_id == msg.wParam:
                        hotkey.callback()
                        return True, 0
            return False, 0

        if self._x11 is not None and self._x11_display is not None:
            try:
                from ctypes import byref, c_int, c_long, c_ubyte, c_uint, c_void_p

                class XKeyEvent(ctypes.Structure):
                    _fields_ = [
                        ("type", c_int), ("serial", c_long), ("send_event", c_int),
                        ("display", c_void_p), ("window", c_ulong), ("root", c_ulong),
                        ("subwindow", c_ulong), ("time", c_ulong), ("x", c_int),
                        ("y", c_int), ("x_root", c_int), ("y_root", c_int),
                        ("state", c_uint), ("keycode", c_int), ("same_screen", c_int),
                    ]

                event = XKeyEvent.from_address(int(message))
                # X11 KeyPress is 2.
                if event.type == 2:
                    for hotkey in self._hotkeys:
                        if hotkey.native_id == (event.keycode | (event.state << 16)):
                            hotkey.callback()
                            return True, 0
            except Exception:  # noqa: BLE001
                log.debug("Native event handling failed", exc_info=True)
        return False, 0


_KEY_NAMES: dict[str, int] = {
    **{chr(code).lower(): value for code, value in _WIN_VK.items() if 0x41 <= value <= 0x5A},
    **{f"f{index}": value for index, value in enumerate(
        [0x70, 0x71, 0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A, 0x7B], start=1)},
    "left": Qt.Key_Left, "right": Qt.Key_Right, "up": Qt.Key_Up, "down": Qt.Key_Down,
    "space": Qt.Key_Space, "tab": Qt.Key_Tab, "esc": Qt.Key_Escape,
    "escape": Qt.Key_Escape, "pause": Qt.Key_Pause, "home": Qt.Key_Home,
    "end": Qt.Key_End, "pageup": Qt.Key_PageUp, "pagedown": Qt.Key_PageDown,
    "pgup": Qt.Key_PageUp, "pgdown": Qt.Key_PageDown,
}


__all__ = [
    "Hotkey",
    "HotkeyManager",
    "DEFAULT_HOTKEYS",
    "global_hotkeys_supported",
]