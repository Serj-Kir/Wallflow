"""Wallpaper rendering: monitor surfaces, transitions and previews."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .engine.base import FitMode, WallpaperRequest
from .live import capabilities as live_capabilities
from .live import create_widget, unavailable_reason
from .models import MonitorInfo, Wallpaper, WallpaperKind
from .window import WallpaperPreview, WallpaperWindow

log = logging.getLogger(__name__)

STATIC_MODE_NATIVE = "native"
STATIC_MODE_WINDOW = "window"
STATIC_MODE_AUTO = "auto"

#: Cross-fade length used when the player changes wallpaper on its own.
DEFAULT_TRANSITION_MS = 800


@dataclass
class MonitorSlot:
    """One screen and the window that paints its wallpaper."""

    info: MonitorInfo
    window: WallpaperWindow

    @property
    def id(self) -> str:
        return self.info.id


class WallpaperPlayer(QObject):
    """Owns one :class:`WallpaperWindow` per monitor and switches their content."""

    wallpaper_changed = Signal(object)       # Wallpaper | None
    error = Signal(str)
    playback_error = Signal(str)

    def __init__(
        self,
        backend_stack,
        config,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend = backend_stack
        self._config = config
        self._slots: list[MonitorSlot] = []
        self._current: Optional[Wallpaper] = None
        self._paused = False
        self._muted = True
        self._transition_ms = DEFAULT_TRANSITION_MS
        self._animations: list[QPropertyAnimation] = []
        self._preview: Optional[WallpaperPreview] = None
        self._windows_visible = True

    # ------------------------------------------------------------- monitors
    def monitors(self) -> list[MonitorInfo]:
        return [slot.info for slot in self._slots]

    def refresh_monitors(self) -> None:
        """Re-create surfaces after a display change (plug, resolution)."""
        infos = self._discover_monitors()
        previous = {slot.id: slot.window for slot in self._slots}
        for slot in self._slots:
            slot.window.close()
            slot.window.deleteLater()
        self._slots.clear()

        for info in infos:
            screen = self._screen_for(info)
            window = WallpaperWindow(screen)
            window.apply_geometry_for(screen) if screen else None
            slot = MonitorSlot(info=info, window=window)
            self._slots.append(slot)
            if info.id in previous:
                old = previous[info.id]
                old.setParent(None)
                old.deleteLater()
        # Recreate content for every slot using the active wallpaper.
        if self._current is not None:
            self._apply_to_all(self._current)

    def _discover_monitors(self) -> list[MonitorInfo]:
        from .engine import monitors_from_qt

        qt_monitors = monitors_from_qt(QGuiApplication.screens())
        backend_monitors = self._backend.primary.monitors() or []
        from .engine import merge_monitors

        merged = merge_monitors(qt_monitors, backend_monitors)
        return merged or qt_monitors

    @staticmethod
    def _screen_for(info: MonitorInfo):
        screens = QGuiApplication.screens()
        # Qt names a screen after the X11/Wayland output it mirrors, which is
        # exactly the id stored on MonitorInfo.
        for screen in screens:
            if screen.name() == info.id:
                return screen
        for screen in screens:
            geometry = screen.geometry()
            if geometry.width() == info.width and geometry.height() == info.height:
                return screen
        return screens[0] if screens else None

    # ----------------------------------------------------------- static mode
    @property
    def static_mode(self) -> str:
        return self._config.get("static_wallpaper_mode", STATIC_MODE_AUTO)

    def _decide_mode(self, kind: WallpaperKind, *, needs_per_monitor: bool) -> str:
        """Pick native vs. window for a static image.

        ``native`` hands the file to the desktop, which is the crispest option
        and the only one that can sit correctly behind icons on Wayland. The
        window renderer is used when the user asked for it, or when the
        desktop's API cannot express what the profile wants - which is the case
        on GNOME/Cinnamon, whose background API holds exactly one image.
        """
        if kind is not WallpaperKind.IMAGE:
            # Animated wallpapers are always composited by us.
            return "window"

        mode = self.static_mode
        if mode == STATIC_MODE_WINDOW:
            return "window"
        if mode == STATIC_MODE_NATIVE:
            return "native"

        # auto
        if needs_per_monitor and not self._backend.capabilities.per_monitor:
            return "window"
        return "native"

    @staticmethod
    def _is_wayland() -> bool:
        import os

        return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")

    # ----------------------------------------------------------------- apply
    @property
    def current(self) -> Optional[Wallpaper]:
        return self._current

    def set_current(
        self,
        wallpaper: Optional[Wallpaper],
        *,
        monitor_map: Optional[dict[str, Optional[Wallpaper]]] = None,
        transition_ms: Optional[int] = None,
    ) -> None:
        """Show ``wallpaper``, honouring per-monitor overrides.

        ``monitor_map`` maps a monitor id to its own wallpaper; monitors not
        present fall back to ``wallpaper``.
        """
        self._current = wallpaper
        if wallpaper is None:
            self.clear()
            self.wallpaper_changed.emit(None)
            return

        overrides = {
            monitor_id: item
            for monitor_id, item in (monitor_map or {}).items()
            if item is not None
        }
        # The wallpaper each screen should end up showing.
        effective = {
            slot.id: overrides.get(slot.id) or wallpaper for slot in self._slots
        }
        needs_per_monitor = (
            len(self._slots) > 1
            and len({id(item) for item in effective.values()}) > 1
        )

        mode = self._decide_mode(
            wallpaper.wallpaper_kind, needs_per_monitor=needs_per_monitor
        )
        if mode == "native":
            self._apply_native_all(effective, needs_per_monitor=needs_per_monitor)
        else:
            self._apply_windows(effective, transition_ms, fallback=wallpaper)

        self.wallpaper_changed.emit(wallpaper)

    def _apply_native_all(
        self, effective: dict[str, Wallpaper], *, needs_per_monitor: bool
    ) -> None:
        """Apply static images through the desktop's own API on every screen."""
        self._hide_all_windows()
        capabilities = self._backend.capabilities

        if not capabilities.per_monitor and needs_per_monitor:
            # Should not be reached: _decide_mode would have picked "window".
            log.warning(
                "Backend %s cannot place wallpapers per monitor; falling back to windows",
                self._backend.primary.name,
            )
            self._apply_windows(effective, fallback=self._current)
            return

        if capabilities.per_monitor:
            for monitor_id, item in effective.items():
                self._apply_native(item, monitor_id)
            return

        # One image for every screen.
        applied_ids: set[int] = set()
        for item in effective.values():
            if id(item) in applied_ids:
                continue
            applied_ids.add(id(item))
            self._apply_native(item, None)

    def _apply_windows(
        self,
        effective: dict[str, Wallpaper],
        transition_ms: Optional[int] = None,
        *,
        fallback: Optional[Wallpaper] = None,
    ) -> None:
        """Render each monitor's wallpaper into its own desktop-level window."""
        # Give the desktop a sensible native wallpaper underneath. If our window
        # is ever covered - a compositor that refuses the desktop layer, a
        # crashed renderer - the user still sees a real wallpaper rather than a
        # black rectangle.
        if fallback is not None and fallback.wallpaper_kind is WallpaperKind.IMAGE:
            self._apply_native(fallback, None, quiet=True)

        for slot in self._slots:
            item = effective.get(slot.id)
            if item is None:
                continue
            self._render_in_window(slot, item, transition_ms)

    def _apply_native(
        self, wallpaper: Wallpaper, monitor_id: Optional[str], *, quiet: bool = False
    ) -> None:
        """Hand one static image to the desktop's own wallpaper API."""
        if wallpaper.wallpaper_kind is not WallpaperKind.IMAGE:
            return
        path = Path(wallpaper.path)
        if not path.exists():
            if not quiet:
                self.error.emit(f"Файл не найден: {path.name}")
            return
        request = WallpaperRequest(path=path, monitor_id=monitor_id or "", fit=FitMode.FILL)
        if not self._backend.set_wallpaper(request) and not quiet:
            self.error.emit("Не удалось применить обои средствами системы")

    # -------------------------------------------------------------- rendering
    def _render_in_window(
        self,
        slot: MonitorSlot,
        wallpaper: Wallpaper,
        transition_ms: Optional[int] = None,
    ) -> None:
        widget = create_widget(wallpaper.path, muted=self._muted)
        if widget is None:
            reason = unavailable_reason(wallpaper.wallpaper_kind)
            self.playback_error.emit(
                f"Не удалось воспроизвести «{wallpaper.name}»: {reason or 'неизвестная причина'}"
            )
            return
        if transition_ms:
            widget.setProperty("opacity", 0.0)
            animation = QPropertyAnimation(widget, b"windowOpacity", self)
            animation.setDuration(max(0, transition_ms))
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.finished.connect(lambda: self._animations.remove(animation) if animation in self._animations else None)
            self._animations.append(animation)
            slot.window.set_content(widget)
            animation.start()
        else:
            slot.window.set_content(widget)
        slot.window.present()
        if not self._paused:
            slot.window.start_content()
        if self._config.get("interactive_web_wallpapers", True) and wallpaper.wallpaper_kind is WallpaperKind.WEB:
            slot.window.set_clickable(True)

    def _hide_all_windows(self) -> None:
        for slot in self._slots:
            slot.window.hide()

    # ------------------------------------------------------------------ clear
    def clear(self) -> None:
        for slot in self._slots:
            slot.window.stop_content()
            slot.window.set_content(None)
            slot.window.hide()
        self._backend.clear()

    # ------------------------------------------------------------ pause/state
    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        for slot in self._slots:
            if paused:
                slot.window.pause_content()
            else:
                slot.window.start_content()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        for slot in self._slots:
            content = slot.window.content
            if hasattr(content, "set_muted"):
                content.set_muted(muted)

    def set_max_fps(self, fps: int) -> None:
        for slot in self._slots:
            content = slot.window.content
            spec = getattr(content, "spec", None)
            if spec is not None and hasattr(content, "_apply_fps"):
                spec.fps = max(1, min(240, fps))
                content._apply_fps()

    def apply_accent(self, hex_color: str) -> None:
        if not hex_color:
            return
        try:
            self._backend.primary.set_accent_color(hex_color)
        except Exception:  # noqa: BLE001
            log.debug("Accent colour not supported", exc_info=True)

    def shutdown(self) -> None:
        for animation in list(self._animations):
            animation.stop()
        self._animations.clear()
        for slot in self._slots:
            slot.window.close()
            slot.window.deleteLater()
        self._slots.clear()

    # --------------------------------------------------------------- preview
    def preview(self, wallpaper: Wallpaper, *, size=(960, 540)) -> WallpaperPreview:
        """Open (or reuse) a floating preview window for ``wallpaper``."""
        if self._preview is None:
            self._preview = WallpaperPreview(f"Wallflow — {wallpaper.name}")
        widget = create_widget(wallpaper.path, muted=False)
        if widget is None:
            self.playback_error.emit(
                f"Не удалось открыть «{wallpaper.name}»: "
                f"{unavailable_reason(wallpaper.wallpaper_kind) or 'неизвестная причина'}"
            )
            return self._preview
        self._preview.setWindowTitle(f"Wallflow — {wallpaper.name}")
        self._preview.resize(*size)
        self._preview.set_content(widget)
        self._preview.show()
        self._preview.raise_()
        return self._preview

    def close_preview(self) -> None:
        if self._preview is not None:
            self._preview.close()
            self._preview.deleteLater()
            self._preview = None

    # ------------------------------------------------------------ diagnostics
    def status_text(self) -> str:
        caps = live_capabilities()
        enabled = [k for k, v in caps.items() if v]
        disabled = [k for k, v in caps.items() if not v]
        text = "Форматы: " + ", ".join(enabled)
        if disabled:
            text += f" (недоступно: {', '.join(disabled)})"
        if self._slots:
            backend = getattr(self._backend.primary, "label", "?")
            text += f" · система: {backend}"
        return text


__all__ = [
    "WallpaperPlayer",
    "MonitorSlot",
    "STATIC_MODE_AUTO",
    "STATIC_MODE_NATIVE",
    "STATIC_MODE_WINDOW",
]