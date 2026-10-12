"""Desktop-level wallpaper windows.

Static wallpapers are usually handed to the OS (crisp, native, survives a
lock screen). Animated ones have to be composited by us, which means a window
that sits at the desktop layer: behind the icons, ignoring the mouse, never
appearing in the task switcher.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from typing import Optional

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from .live.renderer import WallpaperWidget

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

# X11 atoms used to pin the window to the desktop layer.
_NET_WM_WINDOW_TYPE = "_NET_WM_WINDOW_TYPE"
_NET_WM_WINDOW_TYPE_DESKTOP = "_NET_WM_WINDOW_TYPE_DESKTOP"
_NET_WM_STATE_SKIP_PAGER = "_NET_WM_STATE_SKIP_PAGER"
_NET_WM_STATE_SKIP_TASKBAR = "_NET_WM_STATE_SKIP_TASKBAR"
_NET_WM_STATE_STICKY = "_NET_WM_STATE_STICKY"
_NET_WM_WINDOW = "_NET_WM_WINDOW"
_NET_SUPPORTING_WM_CHECK = "_NET_SUPPORTING_WM_CHECK"


#: Signature of ``XErrorHandler``.
X_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

#: Keeps the handler alive for the process lifetime; Xlib holds only a pointer.
_x_error_sink: list = []


def _libx11():
    try:
        x11 = ctypes.CDLL("libX11.so.6")
    except OSError:  # pragma: no cover - non-X11 systems
        return None

    # Xlib aborts the process on any protocol error unless a handler is set.
    # BadWindow/BadAtom are routine here - the window may not be mapped yet, or
    # another client may already own the atom - so they are swallowed.
    try:
        x11.XSetErrorHandler.argtypes = [X_ERROR_HANDLER]
        x11.XSetErrorHandler.restype = ctypes.c_void_p
        handler = X_ERROR_HANDLER(lambda display, event: 0)
        x11.XSetErrorHandler(handler)
        _x_error_sink.append(handler)
    except Exception:  # noqa: BLE001
        log.debug("Could not install an X11 error handler", exc_info=True)
    return x11


def send_to_desktop_layer(widget: QWidget) -> bool:
    """Ask X11 to treat ``widget``'s window as the desktop background.

    Only works under X11 (including XWayland). Compositors on Wayland control
    stacking themselves and ignore these hints, so this is best-effort by
    design.
    """
    if IS_WINDOWS:
        return _send_windows_to_desktop(widget)
    x11 = _libx11()
    if x11 is None or not os.environ.get("DISPLAY"):
        return False
    try:
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
        ]

        display = x11.XOpenDisplay(None)
        if not display:
            return False
        try:
            wid = int(widget.winId())
            type_atom = x11.XInternAtom(display, _NET_WM_WINDOW_TYPE.encode(), False)
            desktop_atom = x11.XInternAtom(
                display, _NET_WM_WINDOW_TYPE_DESKTOP.encode(), False
            )
            # 32 = XA_ATOM
            x11.XChangeProperty(display, wid, type_atom, 32, 0, 1,
                                ctypes.cast(ctypes.c_ulong(desktop_atom), ctypes.c_char_p), 4)
            state_atom = x11.XInternAtom(display, b"_NET_WM_STATE", False)
            for name in (_NET_WM_STATE_SKIP_PAGER, _NET_WM_STATE_SKIP_TASKBAR, _NET_WM_STATE_STICKY):
                atom = x11.XInternAtom(display, name.encode(), False)
                x11.XChangeProperty(display, wid, state_atom, 32, 0, 1,
                                    ctypes.cast(ctypes.c_ulong(atom), ctypes.c_char_p), 4)
            x11.XFlush(display)
            x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x11.XSync(display, 0)
            return True
        finally:
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay(display)
    except Exception:  # noqa: BLE001 - never let a hint break playback
        log.debug("Could not set X11 desktop layer hint", exc_info=True)
        return False


def _send_windows_to_desktop(widget: QWidget) -> bool:
    """Mark the window as a shell "desktop" object on Windows."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = int(widget.winId())
        # 0x0002 = WS_EX_TOOLWINDOW (no alt-tab / taskbar entry)
        user32.SetWindowLongPtrW(hwnd, -20, 0x00000002 | 0x00000080)  # WS_EX_NOACTIVATE
        return True
    except Exception:  # noqa: BLE001
        log.debug("Could not set Windows desktop flags", exc_info=True)
        return False


class WallpaperWindow(QWidget):
    """A single frameless window hosting one wallpaper renderer."""

    def __init__(self, monitor: "QGuiApplication" | object = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._content: Optional[WallpaperWidget] = None
        self._clickable = False
        self._desktop_layer = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnBottomHint
            | Qt.WindowTransparentForInput
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowTitle("Wallflow")

        if isinstance(monitor, QWidget):  # a QScreen is passed in practice
            self.apply_geometry_for(monitor)  # type: ignore[arg-type]

    # ------------------------------------------------------------- geometry
    def apply_geometry_for(self, screen) -> None:
        geometry = screen.geometry()
        dpr = screen.devicePixelRatio() if hasattr(screen, "devicePixelRatio") else 1.0
        # Use device-independent coordinates; Qt scales for us.
        self.setGeometry(
            int(geometry.x() / dpr),
            int(geometry.y() / dpr),
            int(geometry.width() / dpr),
            int(geometry.height() / dpr),
        )

    # --------------------------------------------------------------- content
    def set_content(self, widget: Optional[WallpaperWidget]) -> None:
        if self._content is widget:
            return
        if self._content is not None:
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = widget
        if widget is not None:
            widget.setParent(self)
            widget.setGeometry(self.rect())
            widget.setFit("fill")
            widget.show()
        self.update()

    @property
    def content(self) -> Optional[WallpaperWidget]:
        return self._content

    def set_clickable(self, clickable: bool) -> None:
        """Allow mouse interaction with interactive web wallpapers."""
        if self._clickable == clickable:
            return
        self._clickable = clickable
        flags = (
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | (Qt.NoWindowTransparentForInput if clickable else Qt.WindowTransparentForInput)
        )
        if not clickable:
            flags |= Qt.WindowStaysOnBottomHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not clickable)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not clickable)
        if self.isVisible():
            self.show()

    # ------------------------------------------------------------------ show
    def present(self) -> None:
        """Show the window and pin it to the desktop layer."""
        self._content_layout()
        if not self.isVisible():
            self.show()
        if not self._desktop_layer:
            self._desktop_layer = send_to_desktop_layer(self)
            if self._desktop_layer and self.isVisible():
                # Changing the layer can require a re-show under some WMs.
                self.hide()
                self.show()

    def _content_layout(self) -> None:
        if self._content is not None:
            self._content.setGeometry(self.rect())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._content_layout()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._desktop_layer:
            QTimer.singleShot(0, self._reapply_layer)

    def _reapply_layer(self) -> None:
        if self.isVisible() and not self._desktop_layer:
            self._desktop_layer = send_to_desktop_layer(self)

    # ---------------------------------------------------------------- pause
    def start_content(self) -> None:
        if self._content is not None:
            self._content.start()

    def pause_content(self) -> None:
        if self._content is not None:
            self._content.pause()

    def stop_content(self) -> None:
        if self._content is not None:
            self._content.stop()

    def paintEvent(self, event) -> None:  # noqa: N802
        # Fully transparent: the child renderer owns all the pixels.
        if self._content is None:
            event.accept()
            return
        super().paintEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_content()
        if self._content is not None:
            self._content.setParent(None)
            self._content.deleteLater()
            self._content = None
        super().closeEvent(event)


class WallpaperPreview(QWidget):
    """Small standalone window used by the UI to preview an animated wallpaper."""

    def __init__(self, title: str = "Предпросмотр", parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMinimumSize(480, 270)
        self._content: Optional[WallpaperWidget] = None

    def set_content(self, widget: Optional[WallpaperWidget]) -> None:
        if self._content is widget:
            return
        if self._content is not None:
            self._content.setParent(None)
            self._content.deleteLater()
        self._content = widget
        if widget is not None:
            widget.setParent(self)
            widget.setGeometry(self.rect())
            widget.setFit("fit")
            widget.show()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._content is not None:
            self._content.setGeometry(self.rect())
            self._content.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        if self._content is not None:
            self._content.pause()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._content is not None:
            self._content.setGeometry(self.rect())

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._content is not None:
            self._content.stop()
            self._content.setParent(None)
            self._content.deleteLater()
            self._content = None
        super().closeEvent(event)


__all__ = ["WallpaperWindow", "WallpaperPreview", "send_to_desktop_layer"]