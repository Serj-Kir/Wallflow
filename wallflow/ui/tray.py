"""System tray icon and its menu."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from ..version import APP_NAME

log = logging.getLogger(__name__)


def app_icon(size: int = 256, accent: str = "#5b8cff") -> QIcon:
    """Draw the app icon procedurally so no binary assets are needed."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setBrush(QColor("#1b1c23"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(0, 0, size, size, size * 0.22, size * 0.22)

    # Three stacked "screens" suggesting a multi-monitor wallpaper manager.
    painter.setBrush(QColor(accent))
    painter.drawRoundedRect(
        size * 0.16, size * 0.20, size * 0.44, size * 0.30, size * 0.05, size * 0.05
    )
    painter.setBrush(QColor("#8fb0ff"))
    painter.drawRoundedRect(
        size * 0.40, size * 0.42, size * 0.44, size * 0.34, size * 0.05, size * 0.05
    )
    painter.setBrush(QColor("#3d63b8"))
    painter.drawRoundedRect(
        size * 0.16, size * 0.52, size * 0.22, size * 0.24, size * 0.04, size * 0.04
    )
    painter.end()

    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    """Tray icon exposing playback controls and quick access to the window."""

    show_requested = Signal()
    play_pause_requested = Signal()
    next_requested = Signal()
    previous_requested = Signal()
    open_folders_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent: QWidget | None = None, accent: str = "#5b8cff") -> None:
        super().__init__(parent)
        self._parent = parent
        self._accent = accent
        self.setIcon(app_icon(256, accent))
        self.setToolTip(APP_NAME)

        self._menu = QMenu(parent)
        self._build_menu()
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(4000)
        self._status_timer.timeout.connect(self._refresh_tooltip)

    def _build_menu(self) -> None:
        def add(label: str, slot, *, enabled: bool = True) -> QAction:
            action = QAction(label, self._menu)
            action.triggered.connect(lambda checked=False: slot())
            action.setEnabled(enabled)
            self._menu.addAction(action)
            return action

        add("Открыть Wallflow", self.show_requested.emit)
        self._menu.addSeparator()
        self._current_action = add("▶  Воспроизвести", self.play_pause_requested.emit)
        add("⏭  Следующие", self.next_requested.emit)
        add("⏮  Предыдущие", self.previous_requested.emit)
        self._menu.addSeparator()
        add("Открыть папку обоев", self.open_folders_requested.emit)
        self._menu.addSeparator()
        add("Выход", self.quit_requested.emit)

    def set_playing(self, playing: bool) -> None:
        self._current_action.setText("⏸  Пауза" if playing else "▶  Воспроизвести")

    def set_status(self, text: str) -> None:
        self._status = text
        self._refresh_tooltip()

    _status = ""

    def _refresh_tooltip(self) -> None:
        lines = [APP_NAME]
        if self._status:
            lines.append(self._status)
        self.setToolTip("\n".join(lines))

    def _on_activated(self, reason) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        if reason == QSystemTrayIcon.Trigger:
            self.show_requested.emit()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_requested.emit()

    def notify(self, title: str, message: str, icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.Information) -> None:
        try:
            self.showMessage(title, message, icon, 4000)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            log.debug("Tray notification failed", exc_info=True)


__all__ = ["Tray", "app_icon"]