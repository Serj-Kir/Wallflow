"""Qt widgets that draw wallpapers.

Each widget implements the same tiny contract used by :mod:`wallflow.player`:

``set_source(path)``  load a new wallpaper
``start()`` / ``pause()`` / ``stop()``  playback control
``is_running``        whether frames are currently advancing
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QImageReader,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .scene import SceneSimulator, SceneSpec, hsv_color, parse_color

log = logging.getLogger(__name__)


def _fit_rect(image_size: tuple[int, int], target: QRectF, fit: str, alignment: str) -> QRectF:
    """Scale ``image_size`` into ``target`` following a Wallpaper-Engine-style fit."""
    iw, ih = image_size
    tw, th = target.width(), target.height()
    if iw <= 0 or ih <= 0 or tw <= 0 or th <= 0:
        return target
    scale_x = tw / iw
    scale_y = th / ih

    if fit == "stretch":
        return QRectF(target)
    if fit == "fill":
        s = max(scale_x, scale_y)
        w, h = iw * s, ih * s
    elif fit == "fit":
        s = min(scale_x, scale_y)
        w, h = iw * s, ih * s
    else:  # center — keep native size, clamped to the target
        w, h = min(iw, tw), min(ih, th)

    x = target.x() + (tw - w) / 2
    y = target.y() + (th - h) / 2
    if alignment:
        if "t" in alignment and "b" not in alignment:
            y = target.y()
        elif "b" in alignment and "t" not in alignment:
            y = target.bottom() - h
        if "l" in alignment and "r" not in alignment:
            x = target.x()
        elif "r" in alignment and "l" not in alignment:
            x = target.right() - w
    return QRectF(x, y, w, h)


class WallpaperWidget(QWidget):
    """Common base: transparent background, no focus, no mouse capture."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._source: Optional[Path] = None
        self._running = False

    @property
    def source(self) -> Optional[Path]:
        return self._source

    @property
    def is_running(self) -> bool:
        return self._running

    def set_source(self, path: Path | str) -> None:
        raise NotImplementedError

    def start(self) -> None:
        self._running = True

    def pause(self) -> None:
        self._running = False

    def stop(self) -> None:
        self.pause()

    def set_fit(self, fit: str, alignment: str = "center") -> None:
        self._fit = fit
        self._alignment = alignment
        self.update()

    _fit = "fill"
    _alignment = "center"


class ImageWidget(WallpaperWidget):
    """Static or GIF-animated image wallpaper."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._reader: Optional[QImageReader] = None
        self._animated = False
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._next_frame)

    def set_source(self, path: Path | str) -> None:
        p = Path(path)
        self._stop_animation()
        self._reader = None
        if not p.exists():
            self._image = QImage()
            self.update()
            return

        # supportsAnimation() lives on QImageReader, not QImage. Going through
        # the reader is also what allows jumpToNextImage() to walk GIF frames.
        reader = QImageReader(str(p))
        image = reader.read()
        if image.isNull():
            log.warning("Could not decode image %s: %s", p, reader.errorString())
            self._image = QImage()
        else:
            self._image = image
            if reader.supportsAnimation():
                self._reader = reader
                self._animated = True
                self._animation_timer.start(max(20, image.animationDelay()))
            else:
                self._animated = False
        self._source = p
        self.update()

    def _stop_animation(self) -> None:
        self._animation_timer.stop()
        self._animated = False

    def _next_frame(self) -> None:
        if not self._running:
            return
        # Animated images are multi-frame; QImageReader has to drive them.
        if not self._image.jumpToNextImage():
            if self._source:
                reader = QImageReader(str(self._source))
                self._image = reader.read()
                self._reader = reader
            if self._image.isNull() or not self._animated:
                self._animation_timer.stop()
        self.update()

    def start(self) -> None:
        super().start()
        if self._animated and not self._animation_timer.isActive():
            self._animation_timer.start(max(20, self._image.animationDelay()))
        self.update()

    def pause(self) -> None:
        super().pause()
        self._animation_timer.stop()

    def current_image(self) -> QImage:
        return self._image

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._image.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        target = QRectF(self.rect())
        if self._fit == "tile":
            painter.drawTiledPixmap(self.rect(), self._image)
            return
        rect = _fit_rect(
            (self._image.width(), self._image.height()), target, self._fit, self._alignment
        )
        painter.drawImage(rect, self._image)


class SceneWidget(WallpaperWidget):
    """Animated scene wallpaper driven by :class:`SceneSimulator`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sim = SceneSimulator(SceneSpec.builtin("particles"))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._last = 0.0
        self._frame_ms = 0.0

    def set_source(self, path: Path | str) -> None:
        p = Path(path)
        if p.suffix.lower() == ".scene" and p.exists():
            self._sim = SceneSimulator(SceneSpec.load(p), self.width(), self.height())
        else:
            # Treat the file as a still image with motion.
            self._sim = SceneSimulator(SceneSpec.builtin("particles"), self.width(), self.height())
            self._sim.spec.background.color = str(p)
            self._sim.spec.background.kind = "solid"
            self._sim._seeded = False
        self._source = p
        self._apply_fps()
        self.start()

    def set_spec(self, spec: SceneSpec) -> None:
        self._sim = SceneSimulator(spec, self.width(), self.height())
        self._apply_fps()
        self.start()

    @property
    def spec(self) -> SceneSpec:
        return self._sim.spec

    def _apply_fps(self) -> None:
        interval = max(1, int(1000 / max(1, self._sim.spec.fps)))
        self._timer.setInterval(interval)

    def start(self) -> None:
        super().start()
        import time

        self._last = time.monotonic()
        self._timer.start()

    def pause(self) -> None:
        super().pause()
        self._timer.stop()

    def stop(self) -> None:
        self.pause()
        self._sim.time = 0.0

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        self._sim.resize(int(self.width() * dpr), int(self.height() * dpr))

    def _tick(self) -> None:
        import time

        now = time.monotonic()
        dt = now - self._last
        self._last = now
        if dt < 0 or dt > 1.0:
            dt = 1.0 / max(1, self._sim.spec.fps)
        self._sim.step(dt)
        self.update()

    # ------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802
        sim = self._sim
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        self._paint_background(painter, rect)
        handler = getattr(self, f"_paint_{sim.spec.kind}", None)
        if handler is None:
            handler = self._paint_particles
        handler(painter, rect)

    def _paint_background(self, painter: QPainter, rect: QRectF) -> None:
        bg = self._sim.spec.background
        palette = self._sim.palette()
        if bg.kind == "gradient" and len(palette) >= 2:
            gradient = QLinearGradient(
                rect.topLeft(),
                rect.bottomRight()
                if abs(bg.angle - 90) < 45
                else (rect.topRight() if bg.angle < 90 else rect.bottomLeft()),
            )
            positions = _gradient_stops(len(palette))
            for color, pos in zip(palette, positions):
                gradient.setColorAt(pos, QColor(*color))
            painter.fillRect(rect, QBrush(gradient))
            return
        painter.fillRect(rect, QColor(*palette[0]))

    def _paint_particles(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        rows = sim.sample()
        base_hue = float(sim.spec.param("hue", 210))
        sat = float(sim.spec.param("saturation", 0.65))
        val = float(sim.spec.param("value", 0.95))
        point_size = float(sim.spec.param("point_size", 2.4))
        alpha = float(sim.spec.param("alpha", 0.85))
        area = max(1.0, (sim.width * sim.height) / (1920.0 * 1080.0))
        radius = point_size * math.sqrt(area)
        link = getattr(sim, "_connect_distance", 0.0)

        # Draw links first so dots stay on top and the picture reads cleanly.
        # The pairwise test is O(n^2), so the count is capped: beyond a few
        # hundred particles the lines cost more than they add visually.
        if link > 0 and len(rows) <= 260:
            link_alpha = 0.10 * alpha
            link_squared = link * link
            for i, a in enumerate(rows):
                ax, ay = a[0], a[1]
                for b in rows[i + 1 :]:
                    dx = b[0] - ax
                    dy = b[1] - ay
                    d2 = dx * dx + dy * dy
                    if d2 < link_squared:
                        fade = 1.0 - math.sqrt(d2) / link
                        painter.setPen(
                            QPen(QColor(*hsv_color(base_hue + 40 * fade, sat, val, link_alpha * fade)), 1.0)
                        )
                        painter.drawLine(QPointF(ax, ay), QPointF(b[0], b[1]))

        painter.setPen(Qt.NoPen)
        for row in rows:
            hue = base_hue + row[4] * 40
            size = radius * row[2] * 0.6
            brush = QBrush(QColor(*hsv_color(hue, sat, val, alpha * row[3])))
            painter.setBrush(brush)
            painter.drawEllipse(QPointF(row[0], row[1]), size, size)

    def _paint_starfield(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        for row in sim.sample():
            brightness = 0.35 + 0.65 * (row[2] / 2.2)
            painter.setBrush(QBrush(QColor(235, 240, 255, int(brightness * 235))))
            size = row[2]
            if row[3] > 40:  # brighter, and with a soft halo
                halo = QColor(200, 220, 255, int(brightness * 60))
                painter.setBrush(QBrush(halo))
                painter.drawEllipse(QPointF(row[0], row[1]), size * 3.5, size * 3.5)
            painter.setBrush(QBrush(QColor(245, 248, 255, int(brightness * 255))))
            painter.drawEllipse(QPointF(row[0], row[1]), size, size)

    def _paint_rain(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        color = QColor(*hsv_color(float(sim.spec.param("hue", 200)), 0.15, 0.95, 0.42))
        painter.setPen(QPen(color, 1.4))
        for row in sim.sample():
            x, y, length = row
            painter.drawLine(QPointF(x, y), QPointF(x - length * 0.22, y - length * 2.6))

    def _paint_bubbles(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        painter.setPen(Qt.NoPen)
        for row in sim.sample():
            x, y, radius, _, phase = row
            shade = 0.5 + 0.4 * math.sin(phase + sim.time)
            gradient = QRadialGradient(QPointF(x - radius * 0.3, y - radius * 0.3), radius)
            inner = QColor(255, 255, 255, int(70 + shade * 60))
            outer = QColor(190, 215, 255, int(18 + shade * 26))
            gradient.setColorAt(0.0, inner)
            gradient.setColorAt(1.0, outer)
            painter.setBrush(QBrush(gradient))
            painter.drawEllipse(QPointF(x, y), radius, radius)

    def _paint_waves(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        count = len(sim.sample())
        amplitude = float(sim.spec.param("amplitude", 60))
        wavelength = float(sim.spec.param("wavelength", 420))
        base_y = sim.height * 0.72
        hue = float(sim.spec.param("hue", 200))
        bottom = sim.height + amplitude * 2
        for index in range(count):
            offset = index * (sim.height * 0.055)
            alpha = 0.30 - index * 0.03
            if alpha <= 0.02:
                break
            path = QPainterPath()
            steps = 56
            for step in range(steps + 1):
                x = rect.left() + rect.width() * step / steps
                phase = sim.time * 0.9 + index * 0.55
                y = (
                    base_y
                    + offset
                    + math.sin(x / max(1.0, wavelength) * math.tau + phase) * amplitude
                )
                if step == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            # Close the shape downwards so each wave reads as a filled band.
            path.lineTo(rect.right(), bottom)
            path.lineTo(rect.left(), bottom)
            path.closeSubpath()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(*hsv_color(hue + index * 22, 0.6, 0.9, alpha))))
            painter.drawPath(path)

    def _paint_gradient(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        palette = sim.palette()
        speed = float(sim.spec.param("speed", 0.15))
        shift = (sim.time * speed) % (len(palette) * 100)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        for index, color in enumerate(palette):
            pos = ((index * 100 + shift) / max(1, len(palette) * 100 - 1)) % 1.0
            gradient.setColorAt(min(0.999, max(0.0, pos)), QColor(*color))
        painter.fillRect(rect, QBrush(gradient))

    def _paint_plasma(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        scale = float(sim.spec.param("scale", 3.0))
        painter.setPen(Qt.NoPen)
        cell = max(6.0, float(sim.spec.param("cell", 14.0)))
        columns = int(rect.width() / cell) + 1
        rows = int(rect.height() / cell) + 1
        hue = float(sim.spec.param("hue", 260))
        for row_index in range(rows):
            for col in range(columns):
                x = col * cell
                y = row_index * cell
                value = (
                    math.sin(x / rect.width() * scale * math.tau + sim.time)
                    + math.sin(y / rect.height() * scale * math.tau + sim.time * 1.3)
                    + math.sin((x + y) / rect.width() * scale * math.tau + sim.time * 0.7)
                ) / 3.0
                alpha = 0.10 + 0.18 * (value + 1) / 2
                painter.setBrush(QBrush(QColor(*hsv_color(hue + value * 90, 0.7, 0.95, alpha))))
                painter.drawRect(QRectF(x, y, cell + 1, cell + 1))

    def _paint_breathing(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        palette = sim.palette()
        t = (math.sin(sim.time * float(sim.spec.param("speed", 0.8))) + 1) / 2
        start = QColor(*palette[0])
        end = QColor(*palette[min(1, len(palette) - 1)])
        start.setAlpha(int(255 * (0.35 + 0.65 * t)))
        end.setAlpha(int(255 * (0.35 + 0.65 * (1 - t))))
        gradient = QRadialGradient(rect.center(), max(rect.width(), rect.height()) / 2)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)
        painter.fillRect(rect, QBrush(gradient))

    def _paint_grid(self, painter: QPainter, rect: QRectF) -> None:
        sim = self._sim
        rows = sim.sample()
        hue = float(sim.spec.param("hue", 200))
        if not rows:
            return
        cell_w = sim.width / max(1, len(rows))
        cell_h = sim.height / max(1, len(rows))
        painter.setPen(Qt.NoPen)
        for index, row in enumerate(rows):
            offset = (sim.time * float(sim.spec.param("speed", 0.6)) * cell_h) % (cell_h * 2)
            y = row[1] - offset
            while y < -cell_h:
                y += cell_h * 2
            alpha = 0.10 + 0.28 * abs(math.sin(index * 0.4 + sim.time))
            painter.setBrush(QBrush(QColor(*hsv_color(hue + index * 8, 0.7, 0.95, alpha))))
            painter.drawRect(QRectF(0, y, sim.width, cell_h * 0.8))


def _gradient_stops(count: int) -> list[float]:
    if count <= 1:
        return [0.0]
    return [i / (count - 1) for i in range(count)]


__all__ = ["WallpaperWidget", "ImageWidget", "SceneWidget"]