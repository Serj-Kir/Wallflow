"""The playback bar shown at the bottom of the main window."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ..models import PauseReason, Wallpaper
from ..utils import human_duration

log = logging.getLogger(__name__)


class PlayerBar(QWidget):
    """Wallpaper name, playlist controls and the pause state, all in one row."""

    play_pause = Signal()
    next = Signal()
    previous = Signal()
    stop = Signal()
    shuffle = Signal()
    profile_changed = Signal(str)

    def __init__(self, controller, player, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._player = player
        self.setObjectName("PlayerBar")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self._profile = QComboBox(self)
        self._profile.setMinimumWidth(150)
        self._profile.currentIndexChanged.connect(self._on_profile_selected)

        self._prev_btn = QPushButton("⏮", self)
        self._prev_btn.setFixedWidth(40)
        self._prev_btn.setToolTip("Предыдущие обои")
        self._prev_btn.clicked.connect(self.previous.emit)

        self._play_btn = QPushButton("⏸", self)
        self._play_btn.setFixedWidth(46)
        self._play_btn.setObjectName("Primary")
        self._play_btn.setToolTip("Пауза / воспроизведение")
        self._play_btn.clicked.connect(self.play_pause.emit)

        self._stop_btn = QPushButton("⏹", self)
        self._stop_btn.setFixedWidth(40)
        self._stop_btn.setToolTip("Остановить и вернуть системные обои")
        self._stop_btn.clicked.connect(self.stop.emit)

        self._next_btn = QPushButton("⏭", self)
        self._next_btn.setFixedWidth(40)
        self._next_btn.setToolTip("Следующие обои")
        self._next_btn.clicked.connect(self.next.emit)

        self._name = QLabel("", self)
        self._name.setMinimumWidth(180)
        self._name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self._badge = QLabel("", self)
        self._badge.setObjectName("StatusBadge")

        self._countdown = QProgressBar(self)
        self._countdown.setMaximumWidth(160)
        self._countdown.setTextVisible(False)
        self._countdown.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(6)
        layout.addWidget(self._profile)
        layout.addSpacing(10)
        layout.addWidget(self._prev_btn)
        layout.addWidget(self._play_btn)
        layout.addWidget(self._stop_btn)
        layout.addWidget(self._next_btn)
        layout.addSpacing(10)
        layout.addWidget(self._name, 1)
        layout.addWidget(self._countdown)
        layout.addWidget(self._badge)

        self.refresh_profiles()
        self.refresh()
        self._timer.start()

    # ---------------------------------------------------------------- state
    def refresh_profiles(self) -> None:
        current = self._controller.active_profile.id if self._controller.active_profile else ""
        self._profile.blockSignals(True)
        self._profile.clear()
        for profile in self._controller.profiles:
            self._profile.addItem(profile.name, profile.id)
        index = self._profile.findData(current)
        if index >= 0:
            self._profile.setCurrentIndex(index)
        self._profile.blockSignals(False)

    def _on_profile_selected(self, _index: int) -> None:
        profile_id = self._profile.currentData()
        if profile_id and (not self._controller.active_profile or profile_id != self._controller.active_profile.id):
            self.profile_changed.emit(profile_id)

    def set_wallpaper(self, wallpaper: Optional[Wallpaper]) -> None:
        if wallpaper is None:
            self._name.setText("Обои не выбраны")
            self._name.setToolTip("")
            return
        bits = [wallpaper.name]
        if wallpaper.author:
            bits.append(f"· {wallpaper.author}")
        self._name.setText(" ".join(bits))
        self._name.setToolTip(wallpaper.path)

    def set_paused(self, paused: bool, reason: Optional[PauseReason] = None) -> None:
        self._play_btn.setText("▶" if paused else "⏸")
        labels = {
            PauseReason.USER: "Пауза",
            PauseReason.FULLSCREEN: "Пауза: полный экран",
            PauseReason.BATTERY: "Пауза: батарея",
            PauseReason.LOCKED: "Пауза: блокировка",
            PauseReason.REMOTE: "Пауза: удалённый сеанс",
            PauseReason.SUSPEND: "Пауза: простой",
        }
        text = "▶ Играет" if not paused else labels.get(reason, "Пауза")
        self._badge.setText(text)
        self._badge.setStyleSheet("color: #57ab5a;" if not paused else "color: #c69026;")
        self._play_btn.setToolTip("Возобновить" if paused else "Пауза")

    def _tick(self) -> None:
        """Refresh the countdown to the next automatic wallpaper change."""
        import time

        pending = getattr(self._controller, "_pending_advance_at", 0.0)
        playlist = self._controller.active_playlist
        if not pending or playlist is None or self._controller.paused:
            self._countdown.setVisible(False)
            return
        remaining = pending - time.monotonic()
        if remaining <= 0:
            self._countdown.setVisible(False)
            return
        self._countdown.setVisible(True)
        total = max(1, playlist.interval_seconds)
        self._countdown.setMaximum(total)
        self._countdown.setValue(int(total - remaining))
        self._countdown.setToolTip(f"Смена через {human_duration(remaining)}")

    def refresh(self) -> None:
        self.set_paused(self._controller.paused, self._controller.pause_reason)
        self.set_wallpaper(self._player.current)
        has_playlist = bool(self._controller.active_playlist)
        for button in (self._prev_btn, self._next_btn):
            button.setEnabled(has_playlist)


__all__ = ["PlayerBar"]