"""Library view: the wallpaper grid, search and import."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QItemSelection, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..models import IMAGE_SUFFIXES, SCENE_SUFFIXES, VIDEO_SUFFIXES, Wallpaper, WallpaperKind
from ..utils import human_size
from .widgets import EmptyState, KIND_LABELS, ThumbnailCache, WallpaperDelegate

log = logging.getLogger(__name__)

ALL_SUFFIXES = "|".join(sorted(IMAGE_SUFFIXES | VIDEO_SUFFIXES | SCENE_SUFFIXES))


class LibraryView(QWidget):
    """Grid of every wallpaper Wallflow knows about."""

    apply_requested = Signal(object)        # Wallpaper
    preview_requested = Signal(object)     # Wallpaper
    playlist_add_requested = Signal(object)   # Wallpaper
    sources_requested = Signal()
    status = Signal(str)

    def __init__(self, library, controller, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._controller = controller
        self._cache = ThumbnailCache(int(config.get("thumb_size", 220)))

        self._model = QStandardItemModel(self)
        self._view = QListView(self)
        self._view.setModel(self._model)
        self._view.setViewMode(QListView.IconMode)
        self._view.setResizeMode(QListView.Adjust)
        self._view.setMovement(QListView.Static)
        self._view.setUniformItemSizes(False)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.setSpacing(6)
        self._view.setWordWrap(True)

        self._delegate = WallpaperDelegate(self._cache, self._view)
        self._view.setItemDelegate(self._delegate)
        self._delegate.double_clicked.connect(self._on_double_click)

        self._search = QLineEdit(self)
        self._search.setPlaceholderText("Поиск по названию, автору, тегу…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._schedule_refresh)

        self._kind_filter = QComboBox(self)
        for kind in WallpaperKind:
            self._kind_filter.addItem(KIND_LABELS.get(kind, kind.value), kind.value)
        self._kind_filter.addItem("Все форматы", "")
        self._kind_filter.setCurrentIndex(self._kind_filter.count() - 1)
        self._kind_filter.currentIndexChanged.connect(self._schedule_refresh)

        self._fav_toggle = QPushButton("★ Избранное", self)
        self._fav_toggle.setCheckable(True)
        self._fav_toggle.setChecked(False)
        self._fav_toggle.toggled.connect(self._schedule_refresh)

        self._count_label = QLabel("", self)
        self._count_label.setObjectName("Hint")

        self._buttons = QPushButton("Добавить", self)
        self._buttons.setObjectName("Primary")
        self._buttons.clicked.connect(self._on_add_clicked)
        menu = QMenu(self._buttons)
        menu.addAction("Файлы…", self._on_add_files)
        menu.addAction("Папку…", self._on_add_folder)
        menu.addSeparator()
        menu.addAction("Скачать из интернета…", self._on_open_sources)
        add_menu_button = QPushButton("▾", self)
        add_menu_button.setFixedWidth(30)
        add_menu_button.setMenu(menu)
        self._buttons.setDefault(False)

        self._rescan = QPushButton("Пересканировать", self)
        self._rescan.clicked.connect(self.rescan)

        self._empty = EmptyState(
            "Библиотека пуста",
            "Перетащите изображения или видео сюда,\nлибо добавьте папку и скачайте что-нибудь из интернета.",
            "Добавить файлы",
        )
        self._empty.action.connect(self._on_add_files)

        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._empty)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(self._search, 1)
        header.addWidget(self._kind_filter)
        header.addWidget(self._fav_toggle)
        header.addWidget(self._buttons)
        header.addWidget(add_menu_button)
        header.addWidget(self._rescan)
        layout.addLayout(header)

        layout.addLayout(self._stack)
        layout.addWidget(self._count_label)

        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.doubleClicked.connect(self._on_view_double_click)
        self._view.selectionModel().selectionChanged.connect(self._on_selection_changed)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(180)
        self._refresh_timer.timeout.connect(self.refresh)

        self._pending: dict[str, str] = {}

    # ------------------------------------------------------------------ data
    def selected(self) -> list[Wallpaper]:
        items = self._view.selectedIndexes()
        out: list[Wallpaper] = []
        for index in items:
            wallpaper = index.data(Qt.UserRole)
            if wallpaper is not None:
                out.append(wallpaper)
        return out

    def current(self) -> Optional[Wallpaper]:
        selected = self.selected()
        return selected[0] if selected else None

    def _schedule_refresh(self, *_args) -> None:
        self._refresh_timer.start()

    def refresh(self) -> None:
        kinds: Optional[set[WallpaperKind]] = None
        kind_value = self._kind_filter.currentData()
        if kind_value:
            kinds = {WallpaperKind(kind_value)}

        items = self._library.filtered(
            search=self._search.text(),
            kinds=kinds,
            favourites_only=self._fav_toggle.isChecked(),
        )

        self._model.clear()
        selected_ids = {w.id for w in self.selected()}
        for wallpaper in items:
            entry = QStandardItem()
            entry.setData(wallpaper, Qt.UserRole)
            entry.setEditable(False)
            entry.setToolTip(self._tooltip(wallpaper))
            self._model.appendRow(entry)

        # Re-select what was selected before the rebuild.
        if selected_ids:
            self._restore_selection(selected_ids)

        self._stack.setCurrentWidget(self._view if self._model.rowCount() else self._empty)
        self._count_label.setText(
            f"{self._model.rowCount()} из {len(self._library)} обоев"
            if self._model.rowCount() != len(self._library)
            else f"{self._model.rowCount()} обоев"
        )

    def _restore_selection(self, ids: set[str]) -> None:
        flags = QItemSelection.Select | QItemSelection.Rows
        selection = QItemSelection()
        for row in range(self._model.rowCount()):
            wallpaper = self._model.item(row).data(Qt.UserRole)
            if wallpaper is not None and wallpaper.id in ids:
                selection.merge(
                    self._model.index(row, 0),
                    self._model.index(row, self._model.columnCount() - 1),
                    flags,
                )
        if not selection.isEmpty():
            self._view.selectionModel().select(selection, QItemSelection.ClearAndSelect)

    @staticmethod
    def _tooltip(wallpaper: Wallpaper) -> str:
        lines = [wallpaper.name, f"Тип: {KIND_LABELS.get(wallpaper.wallpaper_kind, '?')}"]
        if wallpaper.author:
            lines.append(f"Автор: {wallpaper.author}")
        try:
            size = wallpaper.file_path.stat().st_size
            lines.append(f"Размер: {human_size(size)}")
        except OSError:
            pass
        if wallpaper.tags:
            lines.append("Теги: " + ", ".join(wallpaper.tags))
        if wallpaper.source_url:
            lines.append(f"Источник: {wallpaper.source_url}")
        return "\n".join(lines)

    # -------------------------------------------------------------- scanning
    def rescan(self) -> None:
        self.status.emit("Сканирование библиотеки…")
        try:
            found = self._library.scan()
        except Exception as exc:  # noqa: BLE001
            log.exception("Library scan failed")
            self.status.emit(f"Ошибка сканирования: {exc}")
            return
        self.refresh()
        self.status.emit(f"Найдено файлов: {found}")

    def reload_library(self) -> None:
        self._library.scan()
        self.refresh()

    # ------------------------------------------------------------- selection
    def _on_selection_changed(self, *_args) -> None:
        wallpaper = self.current()
        if wallpaper is not None:
            self.status.emit(f"Выбрано: {wallpaper.name}")

    def _on_view_double_click(self, index) -> None:
        wallpaper = index.data(Qt.UserRole)
        if wallpaper is not None:
            self.apply_requested.emit(wallpaper)

    def _on_double_click(self, wallpaper: Wallpaper) -> None:
        self.apply_requested.emit(wallpaper)

    # ----------------------------------------------------------- operations
    def _resolution_lines(self, wallpaper: Wallpaper) -> list[str]:
        """Image/video resolution, plus how it compares to the main monitor."""
        size = None
        try:
            from PySide6.QtGui import QImage

            if wallpaper.wallpaper_kind is WallpaperKind.IMAGE:
                image = QImage(wallpaper.path)
                if not image.isNull():
                    size = (image.width(), image.height())
            else:
                from PySide6.QtMultimedia import QMediaPlayer

                player = QMediaPlayer()
                player.setSource(QUrl.fromLocalFile(wallpaper.path))
                for _ in range(60):
                    meta = player.metaData()
                    if meta.isEmpty():
                        QApplication.processEvents()
                        time.sleep(0.05)
                        continue
                    track = meta.value(meta.videoTrack()) if hasattr(meta, "videoTrack") else None
                    if track is not None and track.width() > 0:
                        size = (track.width(), track.height())
                        break
                    QApplication.processEvents()
                    time.sleep(0.05)
                player.stop()
        except Exception:  # noqa: BLE001 - probing must never break the dialog
            return []

        if not size:
            return []

        lines = [f"Разрешение: {size[0]}×{size[1]}"]
        monitors = self._controller.monitors() if self._controller else []
        best = max((m for m in monitors), key=lambda m: m.width * m.height, default=None)
        if best and best.width > 0:
            if size[0] < best.width:
                lines.append(
                    f"<span style='color:#c69026'>Меньше монитора ({best.width}×{best.height}) — "
                    f"при показе будет растянуто в {best.width / size[0]:.1f} раз и станет размытым</span>"
                )
            else:
                lines.append(f"Монитор: {best.width}×{best.height} — разрешения хватает")
        return lines

    def _on_context_menu(self, pos) -> None:
        wallpaper = self.current()
        menu = QMenu(self)
        if wallpaper is None:
            menu.addAction("Обновить список", self.refresh)
            menu.addAction("Пересканировать", self.rescan)
            menu.exec(self._view.viewport().mapToGlobal(pos))
            return

        many = len(self.selected()) > 1
        menu.addAction(
            f"Установить обои{' (выбрано %d)' % len(self.selected()) if many else ''}",
            lambda: self.apply_requested.emit(wallpaper),
        )
        menu.addAction("Предпросмотр", lambda: self.preview_requested.emit(wallpaper))
        menu.addSeparator()
        menu.addAction(
            "★ В избранное" if not wallpaper.favourite else "☆ Убрать из избранного",
            self._toggle_favourite,
        )
        menu.addAction("В плейлист…", lambda: self.playlist_add_requested.emit(wallpaper))
        menu.addAction("Свойства…", self._show_properties)
        menu.addSeparator()
        menu.addAction("Открыть папку", self._reveal)
        menu.addAction("Копировать путь", self._copy_path)
        menu.addAction("Удалить из библиотеки", self._delete)
        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _toggle_favourite(self) -> None:
        for wallpaper in self.selected():
            self._library.toggle_favourite(wallpaper.id)
        self.refresh()

    def _reveal(self) -> None:
        wallpaper = self.current()
        if wallpaper is None:
            return
        from ..utils import which, run

        folder = wallpaper.file_path.parent
        if which("xdg-open"):
            run(["xdg-open", str(folder)], timeout=6)
        elif which("explorer"):
            run(["explorer", "/select," + str(wallpaper.file_path)])

    def _copy_path(self) -> None:
        wallpaper = self.current()
        if wallpaper is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(wallpaper.path)
        self.status.emit("Путь скопирован")

    def _delete(self) -> None:
        chosen = self.selected()
        if not chosen:
            return
        delete_file = QMessageBox.question(
            self,
            "Удалить обои",
            f"Удалить {len(chosen)} файл(ов) с диска?\n\n"
            "Если выбрать «Нет», они будут просто убраны из библиотеки.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if delete_file == QMessageBox.Cancel:
            return
        for wallpaper in chosen:
            self._library.remove(wallpaper.id, delete_file=(delete_file == QMessageBox.Yes))
        self.refresh()
        self.status.emit(
            f"Удалено: {len(chosen)}" + (" (с диска)" if delete_file == QMessageBox.Yes else "")
        )

    def _show_properties(self) -> None:
        wallpaper = self.current()
        if wallpaper is None:
            return
        lines = [
            f"<b>{wallpaper.name}</b>",
            "",
            f"Файл: <code>{wallpaper.path}</code>",
            f"Тип: {KIND_LABELS.get(wallpaper.wallpaper_kind, '?')}",
        ]
        try:
            lines.append(f"Размер: {human_size(wallpaper.file_path.stat().st_size)}")
        except OSError:
            pass
        lines.extend(self._resolution_lines(wallpaper))
        if wallpaper.author:
            lines.append(f"Автор: {wallpaper.author}")
        if wallpaper.description:
            lines.append(f"Описание: {wallpaper.description}")
        if wallpaper.tags:
            lines.append("Теги: " + ", ".join(wallpaper.tags))
        if wallpaper.source_url:
            lines.append(f"Источник: <a href='{wallpaper.source_url}'>{wallpaper.source_url}</a>")
        box = QMessageBox(self)
        box.setWindowTitle("Свойства")
        box.setTextFormat(Qt.RichText)
        box.setText("<br>".join(lines))
        box.exec()

    # ----------------------------------------------------------------- import
    def _on_add_clicked(self) -> None:
        self._on_add_files()

    def _on_add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите обои",
            str(Path.home()),
            "Обои (%s);;Веб-проекты (index.html);;Все файлы (*)" % ALL_SUFFIXES,
        )
        if paths:
            self.import_paths(paths)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с обоями", str(Path.home()))
        if folder:
            self.import_paths([folder])

    def _on_open_sources(self) -> None:
        self.sources_requested.emit()

    def import_paths(self, paths: list[str], *, move: bool = False) -> int:
        """Add dropped or picked files/folders to the library."""
        added = 0
        for entry in paths:
            path = Path(entry)
            if path.is_dir():
                count = self._library.import_dir(path, move=move)
                added += count
            elif path.is_file():
                if self._library.import_file(path, move=move) is not None:
                    added += 1
        if added:
            self.refresh()
            self.status.emit(f"Добавлено обоев: {added}")
        else:
            self.status.emit("Подходящих файлов не найдено")
        return added

    def drop_files(self, paths: list[str]) -> int:
        return self.import_paths(paths)

    # ------------------------------------------------------------------ misc
    def select_first(self) -> None:
        if self._model.rowCount():
            self._view.setCurrentIndex(self._model.index(0, 0))

    def set_thumb_size(self, size: int) -> None:
        self._cache.set_size(size)
        self._view.doItemsLayout()

    @property
    def cache(self) -> ThumbnailCache:
        return self._cache


__all__ = ["LibraryView"]