"""Reusable UI pieces: cached thumbnails, wallpaper cards, drop areas."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..models import Wallpaper, WallpaperKind

log = logging.getLogger(__name__)

KIND_LABELS = {
    WallpaperKind.IMAGE: "Фото",
    WallpaperKind.VIDEO: "Видео",
    WallpaperKind.WEB: "HTML",
    WallpaperKind.SCENE: "Сцена",
}

KIND_ICONS = {
    WallpaperKind.IMAGE: "",
    WallpaperKind.VIDEO: "▶",
    WallpaperKind.WEB: "</>",
    WallpaperKind.SCENE: "✦",
}


class ThumbnailCache:
    """Generates and caches wall-sized thumbnails on disk.

    Decoding a 4K JPEG takes long enough to stutter a grid, so thumbnails are
    written once into ``thumbs/`` and reloaded instantly afterwards.
    """

    def __init__(self, size: int = 220) -> None:
        from .. import paths

        self._dir = paths.thumb_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._size = size
        self._memory: dict[str, QPixmap] = {}
        self._pending: set[str] = set()

    def set_size(self, size: int) -> None:
        size = max(80, min(512, int(size)))
        if size == self._size:
            return
        self._size = size
        self._memory.clear()

    @property
    def size(self) -> int:
        return self._size

    def key(self, wallpaper: Wallpaper) -> str:
        try:
            mtime = int(wallpaper.file_path.stat().st_mtime)
        except OSError:
            mtime = 0
        return f"{wallpaper.id}_{self._size}_{mtime}"

    def get(self, wallpaper: Wallpaper) -> Optional[QPixmap]:
        key = self.key(wallpaper)
        cached = self._memory.get(key)
        if cached is not None:
            return cached
        path = self._dir / f"{key}.png"
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._memory[key] = pixmap
                return pixmap
        if key not in self._pending:
            self._pending.add(key)
            QTimer.singleShot(0, lambda: self._generate(wallpaper, key))
        return None

    def _generate(self, wallpaper: Wallpaper, key: str) -> None:
        try:
            source = wallpaper.file_path
            pixmap = self._render(source, wallpaper.wallpaper_kind)
            if pixmap is not None:
                self._memory[key] = pixmap
                pixmap.save(str(self._dir / f"{key}.png"), "PNG")
        except Exception:  # noqa: BLE001 - a broken thumbnail must not break the grid
            log.debug("Thumbnail failed for %s", wallpaper.path, exc_info=True)
        finally:
            self._pending.discard(key)

    def _render(self, source: Path, kind: WallpaperKind) -> Optional[QPixmap]:
        if kind in (WallpaperKind.VIDEO, WallpaperKind.WEB, WallpaperKind.SCENE):
            pixmap = self._render_video_frame(source)
            if pixmap is not None:
                return pixmap
            return self._placeholder(kind)
        image = QImage(str(source))
        if image.isNull():
            return self._placeholder(kind)
        scaled = image.scaled(
            self._size, self._size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        pixmap = QPixmap.fromImage(scaled)
        return _center_crop(pixmap, self._size, self._size)

    def _render_video_frame(self, source: Path) -> Optional[QPixmap]:
        """Grab a still from a video so it is not represented by a blank card."""
        if source.suffix.lower() not in {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v"}:
            return None
        player = None
        try:
            from PySide6.QtCore import QEventLoop, QTimer, QUrl
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

            player = QMediaPlayer()
            sink = QVideoSink()
            player.setVideoSink(sink)
            player.setSource(QUrl.fromLocalFile(str(source)))

            holder: list[QPixmap] = []
            loop = QEventLoop()

            def on_frame(frame) -> None:
                if frame.isValid():
                    image = frame.toImage()
                    if not image.isNull():
                        holder.append(QPixmap.fromImage(image))
                player.stop()
                loop.quit()

            sink.videoFrameChanged.connect(on_frame)
            QTimer.singleShot(4000, loop.quit)  # never hang the UI on a bad file
            player.play()
            if not loop.isRunning():
                loop.exec()
            player.stop()
            try:
                sink.videoFrameChanged.disconnect(on_frame)
            except (RuntimeError, TypeError):
                pass

            if holder:
                pixmap = holder[0]
                scaled = pixmap.scaledToWidth(self._size, Qt.SmoothTransformation)
                return _center_crop(scaled, self._size, self._size)
        except Exception:  # noqa: BLE001
            log.debug("Video thumbnail failed for %s", source, exc_info=True)
        finally:
            if player is not None:
                try:
                    player.stop()
                except Exception:  # noqa: BLE001
                    pass
        return None

    def _placeholder(self, kind: WallpaperKind) -> QPixmap:
        pixmap = QPixmap(self._size, self._size)
        pixmap.fill(QColor("#272932"))
        painter = QPainter(pixmap)
        painter.setPen(QColor("#7d8494"))
        font = painter.font()
        font.setPointSize(int(self._size * 0.22))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, KIND_ICONS.get(kind, "★"))
        painter.end()
        return pixmap

    def clear(self) -> None:
        self._memory.clear()


def _center_crop(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    if pixmap.width() == width and pixmap.height() == height:
        return pixmap
    scaled = pixmap.scaled(width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


class WallpaperDelegate(QStyledItemDelegate):
    """Paints a grid card: thumbnail, name, kind badge and favourite star."""

    double_clicked = Signal(object)
    context_requested = Signal(object, QPoint)

    CARD_MARGIN = 8

    def __init__(self, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(parent)
        self._cache = cache

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        side = self._cache.size
        return QSize(side, side + 54)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """Draw one grid card. Qt passes its own painter, so we never create one."""
        wallpaper: Wallpaper | None = index.data(Qt.UserRole)
        if wallpaper is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        rect = option.rect.adjusted(
            self.CARD_MARGIN, self.CARD_MARGIN, -self.CARD_MARGIN, -self.CARD_MARGIN
        )
        side = self._cache.size
        image_rect = QRect(rect.left(), rect.top(), min(rect.width(), side), side)

        path = QPainterPath()
        path.addRoundedRect(image_rect, 8, 8)
        if selected:
            painter.setPen(QPen(QColor("#5b8cff"), 2))
        elif hovered:
            painter.setPen(QPen(QColor("#454954"), 1))
        else:
            painter.setPen(Qt.NoPen)
        if selected or hovered:
            painter.drawPath(path)

        pixmap = self._cache.get(wallpaper)
        if pixmap is not None:
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(image_rect.topLeft(), pixmap)
            painter.restore()
        else:
            painter.fillPath(path, QColor("#202128"))

        # Kind badge, bottom-left over the thumbnail.
        badge = KIND_LABELS.get(wallpaper.wallpaper_kind, "")
        if badge:
            font = painter.font()
            font.setPointSize(9)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(badge).adjusted(6, 2, -6, -2)
            chip = QRect(image_rect.left() + 6, image_rect.bottom() - text_rect.height() - 6,
                         text_rect.width(), text_rect.height())
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(chip, 6, 6)
            painter.setPen(QColor("#dfe3ec"))
            painter.drawText(chip, Qt.AlignCenter, badge)

        if wallpaper.favourite:
            painter.setPen(QColor("#ffce54"))
            font = painter.font()
            font.setPointSize(13)
            painter.setFont(font)
            painter.drawText(
                QRect(image_rect.right() - 26, image_rect.top() + 4, 22, 22),
                Qt.AlignCenter, "★",
            )

        # Name + author under the thumbnail.
        font = painter.font()
        font.setPointSize(10)
        painter.setFont(font)
        text_rect = QRect(rect.left(), image_rect.bottom() + 6, rect.width(), 40)
        metrics = painter.fontMetrics()
        name = metrics.elidedText(wallpaper.name, Qt.ElideMiddle, text_rect.width())
        painter.setPen(QColor("#e6e8ee"))
        painter.drawText(text_rect.adjusted(4, 0, -4, -18), Qt.AlignLeft | Qt.AlignVCenter, name)
        subtitle = wallpaper.author or KIND_LABELS.get(wallpaper.wallpaper_kind, "")
        painter.setPen(QColor("#9aa0ae"))
        painter.drawText(
            text_rect.adjusted(4, 20, -4, 0), Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(subtitle, Qt.ElideRight, text_rect.width()),
        )
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() == QEvent.MouseButtonDblClick:
            wallpaper = index.data(Qt.UserRole)
            if wallpaper is not None:
                self.double_clicked.emit(wallpaper)
            return True
        # Selection, hover and context menus all keep QListView's defaults.
        return super().editorEvent(event, model, option, index)


class DropArea(QFrame):
    """A drop target that accepts files from the file manager."""

    files_dropped = Signal(list)
    urls_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None, text: str = "Перетащите файлы сюда") -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("Card")
        self._text = text
        self._active = False

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._active = True
            self._repaint_border()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._active = False
        self._repaint_border()

    def dropEvent(self, event) -> None:  # noqa: N802
        self._active = False
        self._repaint_border()
        mime = event.mimeData()
        if not mime.hasUrls():
            event.ignore()
            return
        paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
        event.acceptProposedAction()
        if paths:
            self.files_dropped.emit(paths)

    def _repaint_border(self) -> None:
        self.setStyleSheet(
            f"QFrame#Card {{ border: 2px dashed {'#5b8cff' if self._active else '#32353f'};"
            f" border-radius: 10px; background-color: {'#202128' if self._active else 'transparent'}; }}"
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QColor("#5a5f6b"))
        font = painter.font()
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        painter.end()


class EmptyState(QWidget):
    """Friendly placeholder shown when a list has nothing in it."""

    action = Signal()

    def __init__(self, title: str, hint: str = "", button: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("font-size: 15px; color: #9aa0ae;")
        layout.addWidget(label)
        if hint:
            sub = QLabel(hint)
            sub.setAlignment(Qt.AlignCenter)
            sub.setWordWrap(True)
            sub.setStyleSheet("color: #5f6572; font-size: 12px;")
            layout.addWidget(sub)
        if button:
            btn = QPushButton(button)
            btn.setObjectName("Primary")
            btn.clicked.connect(self.action.emit)
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(btn)
            row.addStretch()
            layout.addSpacing(6)
            layout.addLayout(row)


def section(title: str) -> tuple[QWidget, QVBoxLayout]:
    """A titled group box plus its layout."""
    box = QFrame()
    box.setObjectName("Card")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 12, 14, 14)
    layout.setSpacing(8)
    label = QLabel(title)
    label.setObjectName("H2")
    layout.addWidget(label)
    return box, layout


def horizontal(*widgets, spacing: int = 8, stretch_last: bool = False) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setSpacing(spacing)
    for widget in widgets:
        if isinstance(widget, QWidget):
            layout.addWidget(widget)
        else:
            layout.addLayout(widget)
    if stretch_last:
        layout.addStretch()
    return layout


def icon_button(text: str, tooltip: str = "", object_name: str = "") -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(tooltip)
    button.setFixedWidth(34)
    button.setCursor(Qt.PointingHandCursor)
    if object_name:
        button.setObjectName(object_name)
    return button


def wallpaper_menu(parent, actions: dict) -> QMenu:
    """Build a context menu from ``{label: callback}``."""
    menu = QMenu(parent)
    for label, callback in actions.items():
        if label == "-":
            menu.addSeparator()
            continue
        action = QAction(label, parent)
        if callback is not None:
            action.triggered.connect(lambda checked=False, cb=callback: cb())
        menu.addAction(action)
    return menu


__all__ = [
    "ThumbnailCache",
    "WallpaperDelegate",
    "DropArea",
    "EmptyState",
    "KIND_LABELS",
    "section",
    "horizontal",
    "icon_button",
    "wallpaper_menu",
]