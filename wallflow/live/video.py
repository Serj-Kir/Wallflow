"""Video wallpaper playback built on Qt Multimedia."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimedia import QVideoSink
from PySide6.QtWidgets import QWidget

from .renderer import WallpaperWidget, _fit_rect

log = logging.getLogger(__name__)


class _FrameReceiver(QObject):
    """Turns a ``QVideoSink`` signal into something a QWidget can paint."""

    frame_ready = Signal()

    def __init__(self, sink: QVideoSink) -> None:
        super().__init__()
        self._frame: Optional[QVideoFrame] = None
        self._size = QSize(0, 0)
        self._pixmap = QPixmap()
        sink.videoFrameChanged.connect(self._on_frame)

    def _on_frame(self, frame: QVideoFrame) -> None:
        if frame.isValid():
            self._frame = frame
            image = frame.toImage()
            if not image.isNull():
                # toImage() allocates each frame; converting once into a
                # pixmap keeps painting cheap for the renderer thread.
                self._pixmap = QPixmap.fromImage(image)
                self._size = self._pixmap.size()
            self.frame_ready.emit()

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def size(self) -> QSize:
        return self._size


class VideoWidget(WallpaperWidget):
    """Loops a video file and paints it inside the wallpaper window."""

    error = Signal(str)

    def __init__(self, muted: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._muted = muted
        self._player = QMediaPlayer(self)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._audio = QAudioOutput(self._player)
        self._audio.setVolume(0.0 if muted else 0.5)
        self._receiver = _FrameReceiver(self._sink)
        self._receiver.frame_ready.connect(self.update)
        self._player.errorOccurred.connect(self._on_error)
        self._player.mediaStatusChanged.connect(self._on_status)
        self._loops = 1
        self._last_error = ""

    # ------------------------------------------------------------------ api
    def set_source(self, path: Path | str) -> None:
        p = Path(path)
        if not p.exists():
            self.error.emit(f"Файл не найден: {p.name}")
            return
        self._source = p
        from PySide6.QtCore import QUrl

        self._player.setSource(QUrl.fromLocalFile(str(p.resolve())))
        self._player.setLoops(self._loops)
        self.start()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self._audio.setVolume(0.0 if muted else 0.5)

    def set_loops(self, loops: int) -> None:
        """``-1`` loops forever; any positive number stops after that many."""
        self._loops = loops
        self._player.setLoops(loops)

    def set_muted_for_video(self, muted: bool) -> None:  # alias kept for clarity
        self.set_muted(muted)

    def start(self) -> None:
        super().start()
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def pause(self) -> None:
        super().pause()
        self._player.pause()

    def stop(self) -> None:
        super().stop()
        self._player.stop()

    def seek(self, position_ms: int) -> None:
        self._player.setPosition(max(0, position_ms))

    def position_ms(self) -> int:
        return self._player.position()

    def duration_ms(self) -> int:
        return self._player.duration()

    def playback_state(self) -> QMediaPlayer.PlaybackState:
        return self._player.playbackState()

    def player(self) -> QMediaPlayer:
        return self._player

    # ------------------------------------------------------------ callbacks
    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.LoadedMedia and not self._running:
            self._player.pause()

    def _on_error(self, error, message: str = "") -> None:
        detail = message or getattr(error, "errorString", lambda: "неизвестная ошибка")()
        self._last_error = detail
        log.warning("Video error: %s", detail)
        self.error.emit(detail)

    # ------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        pixmap = self._receiver.pixmap()
        if pixmap.isNull():
            self._paint_placeholder()
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        rect = _fit_rect(
            (pixmap.width(), pixmap.height()), QRectF(self.rect()), self._fit, self._alignment
        )
        painter.drawPixmap(rect, pixmap)

    def _paint_placeholder(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 12, 18))
        painter.setPen(QColor(150, 150, 165))
        text = "▶  " + (self._source.name if self._source else "нет файла")
        painter.drawText(self.rect(), Qt.AlignCenter, text)


__all__ = ["VideoWidget"]