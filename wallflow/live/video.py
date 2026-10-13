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


#: QMediaPlayer.setLoops() value that repeats the file forever.
LOOPS_FOREVER = -1
#: Play the file exactly once and then stop.
LOOPS_ONCE = 1


class VideoWidget(WallpaperWidget):
    """Loops a video file and paints it inside the wallpaper window."""

    error = Signal(str)

    def __init__(
        self,
        muted: bool = True,
        loops: int = LOOPS_FOREVER,
        parent: QWidget | None = None,
    ) -> None:
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
        self._loops = loops
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
        # Applied after setSource: a new source resets the loop counter.
        self._player.setLoops(self._loops)
        self.start()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self._audio.setVolume(0.0 if muted else 0.5)

    def set_loops(self, loops: int) -> None:
        """``LOOPS_FOREVER`` repeats indefinitely, ``LOOPS_ONCE`` stops at the end."""
        self._loops = loops
        self._player.setLoops(loops)
        if self._restart_needed():
            self._rewind_and_play()

    @property
    def loops(self) -> int:
        return self._loops

    def _restart_needed(self) -> bool:
        """True when playback stopped on the final frame and should roll over."""
        return (
            self._running
            and self._player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
        )

    def _rewind_and_play(self) -> None:
        self._player.setPosition(0)
        self._player.play()

    def start(self) -> None:
        super().start()
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()
        if self._restart_needed():
            # A previous run may have ended on the last frame; without this the
            # widget would sit still while claiming to be playing.
            self._rewind_and_play()

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
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            # QMediaPlayer loops on its own, but it can still stop on the last
            # frame (buffer hiccup, source reload). Rolling over here
            # guarantees a wallpaper never freezes into a still image.
            if self._loops == LOOPS_FOREVER and self._running:
                self._player.setPosition(0)
                self._player.play()

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
        target = _fit_rect(
            (pixmap.width(), pixmap.height()), QRectF(self.rect()), self._fit, self._alignment
        )
        # QPainter has no drawPixmap(QRectF, QPixmap) overload - the scaled
        # variant requires the source rectangle as a third argument.
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.end()

    def _paint_placeholder(self) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(12, 12, 18))
        painter.setPen(QColor(150, 150, 165))
        text = "▶  " + (self._source.name if self._source else "нет файла")
        painter.drawText(self.rect(), Qt.AlignCenter, text)


__all__ = ["VideoWidget"]