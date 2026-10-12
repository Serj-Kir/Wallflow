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
from .utils import IS_LINUX
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

    def _decide_mode(self, kind: WallpaperKind, *, per_monitor_override: bool) -> str:
        """Pick native vs. window for a static image.

        ``native`` hands the file to the desktop, which is the crispest option
        and the only one that can sit correctly behind icons on Wayland. The
        window renderer is used when the user asked for it, or when the
        desktop's API cannot express what the profile wants.
        """
        mode = self.static_mode
        if mode == STATIC_MODE_WINDOW:
            return "window"
        if mode == STATIC_MODE_NATIVE:
            return "native"
        # auto
        capabilities = self._backend.capabilities
        if per_monitor_override and not capabilities.per_monitor:
            # The desktop only supports one image for every screen, so a
            # per-monitor profile has to be composited by us.
            return "window"
        if IS_LINUX and self._is_wayland() and not capabilities.per_monitor:
            return "native"
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

        if monitor_map:
            for slot in self._slots:
                override = monitor_map.get(slot.id)
                if override is not None:
                    self._apply_slot(slot, override, transition_ms)
            remaining = [s for s in self._slots if s.id not in monitor_map]
        else:
            remaining = list(self._slots)

        if remaining:
            self._apply_to_all(wallpaper, slots=remaining, transition_ms=transition_ms)
        self.wallpaper_changed.emit(wallpaper)

    def _apply_to_all(
        self,
        wallpaper: Wallpaper,
        *,
        slots: Optional[list[MonitorSlot]] = None,
        transition_ms: Optional[int] = None,
    ) -> None:
        targets = slots if slots is not None else self._slots
        mode = self._decide_mode(wallpaper.wallpaper_kind, per_monitor_override=False)
        if mode == "native":
            self._hide_all_windows()
            self._apply_native(wallpaper, slot_ids=[s.id for s in targets])
        else:
            for slot in targets:
                self._apply_slot(slot, wallpaper, transition_ms)

    def _apply_slot(
        self,
        slot: MonitorSlot,
        wallpaper: Wallpaper,
        transition_ms: Optional[int] = None,
    ) -> None:
        mode = self._decide_mode(
            wallpaper.wallpaper_kind, per_monitor_override=slot.id != (self._current.id if self._current else slot.id)
        )
        if mode == "native":
            slot.window.hide()
            self._apply_native(wallpaper, slot_ids=[slot.id])
            return
        self._apply_native(wallpaper, slot_ids=[], fallback_only=False)
        self._render_in_window(slot, wallpaper, transition_ms)

    def _apply_native(
        self,
        wallpaper: Wallpaper,
        *,
        slot_ids: list[str],
        fallback_only: bool = True,
    ) -> None:
        """Hand a static image to the desktop's own wallpaper API."""
        kind = wallpaper.wallpaper_kind
        if kind is not WallpaperKind.IMAGE:
            return
        path = Path(wallpaper.path)
        if not path.exists():
            self.error.emit(f"Файл не найден: {path.name}")
            return
        ids = slot_ids or [s.id for s in self._slots]
        applied = False
        for monitor_id in ids:
            request = WallpaperRequest(path=path, monitor_id=monitor_id, fit=FitMode.FILL)
            if self._backend.set_wallpaper(request):
                applied = True
        if not applied and not fallback_only:
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