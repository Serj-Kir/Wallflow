"""Playlist view: build ordered or shuffled sequences of wallpapers."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..models import Wallpaper
from ..utils import human_duration
from .widgets import ThumbnailCache, WallpaperDelegate, section

log = logging.getLogger(__name__)

INTERVAL_CHOICES = [
    ("Только вручную", 0),
    ("Каждые 5 минут", 300),
    ("Каждые 15 минут", 900),
    ("Каждые 30 минут", 1800),
    ("Каждый час", 3600),
    ("Каждые 3 часа", 10800),
    ("Каждые 6 часов", 21600),
    ("Раз в сутки", 86400),
]


class PlaylistsView(QWidget):
    """Left: playlist list. Right: its contents and playback settings."""

    status = Signal(str)
    activated = Signal()

    def __init__(self, library, controller, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._controller = controller
        self._config = config
        self._cache = ThumbnailCache(140)
        self._loading = False

        self._list = QListWidget(self)
        self._list.setMaximumWidth(220)
        self._list.currentItemChanged.connect(self._on_playlist_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._start())

        self._new_btn = QPushButton("+ Создать", self)
        self._new_btn.clicked.connect(self._create_playlist)
        self._rename_btn = QPushButton("Переименовать", self)
        self._rename_btn.clicked.connect(self._rename_playlist)
        self._delete_btn = QPushButton("Удалить", self)
        self._delete_btn.setObjectName("Danger")
        self._delete_btn.clicked.connect(self._delete_playlist)
        self._start_btn = QPushButton("Запустить", self)
        self._start_btn.setObjectName("Primary")
        self._start_btn.clicked.connect(self._start)

        # --- contents ---
        self._contents = QListWidget(self)
        self._contents.setViewMode(QListWidget.IconMode)
        self._contents.setResizeMode(QListWidget.Adjust)
        self._contents.setMovement(QListWidget.Static)
        self._contents.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._contents.setWordWrap(True)
        self._contents.setContextMenuPolicy(Qt.CustomContextMenu)
        self._contents.customContextMenuRequested.connect(self._on_contents_menu)
        delegate = WallpaperDelegate(self._cache, self._contents)
        delegate.double_clicked.connect(lambda _w: self._start())
        self._contents.setItemDelegate(delegate)

        self._shuffle = QComboBox(self)
        self._shuffle.addItem("По порядку", False)
        self._shuffle.addItem("Случайно", True)

        self._interval = QComboBox(self)
        for label, seconds in INTERVAL_CHOICES:
            self._interval.addItem(label, seconds)

        self._transition = QComboBox(self)
        self._transition.addItems(["Без перехода", "Затухание", "Сдвиг", "Масштаб", "Затёртка"])
        self._transition_ms = QSpinBox(self)
        self._transition_ms.setRange(0, 5000)
        self._transition_ms.setSingleStep(100)
        self._transition_ms.setSuffix(" мс")

        for widget in (self._shuffle, self._interval, self._transition):
            widget.currentIndexChanged.connect(self._save_playlist_settings)
        self._transition.currentIndexChanged.connect(self._on_transition_changed)

        self._info = QLabel("", self)
        self._info.setObjectName("Hint")

        self._up_btn = QPushButton("↑", self)
        self._up_btn.clicked.connect(lambda: self._move_items(-1))
        self._down_btn = QPushButton("↓", self)
        self._down_btn.clicked.connect(lambda: self._move_items(1))
        self._remove_btn = QPushButton("Убрать", self)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn = QPushButton("Очистить", self)
        self._clear_btn.clicked.connect(self._clear_playlist)
        self._sort_btn = QPushButton("Сортировать A→Z", self)
        self._sort_btn.clicked.connect(self._sort_playlist)

        content_buttons = QHBoxLayout()
        for button in (self._up_btn, self._down_btn, self._sort_btn, self._remove_btn, self._clear_btn):
            content_buttons.addWidget(button)
        content_buttons.addStretch()

        left = QVBoxLayout()
        left.addWidget(self._list)
        left.addWidget(self._new_btn)
        left.addWidget(self._rename_btn)
        left.addWidget(self._delete_btn)
        left.addWidget(self._start_btn)

        settings_box, settings_layout = section("Воспроизведение")
        grid = QHBoxLayout()
        grid.addWidget(QLabel("Порядок:"))
        grid.addWidget(self._shuffle)
        grid.addSpacing(16)
        grid.addWidget(QLabel("Смена через:"))
        grid.addWidget(self._interval)
        grid.addSpacing(16)
        grid.addWidget(QLabel("Переход:"))
        grid.addWidget(self._transition)
        grid.addWidget(self._transition_ms)
        grid.addStretch()
        settings_layout.addLayout(grid)

        right = QVBoxLayout()
        right.addWidget(self._contents, 1)
        right.addLayout(content_buttons)
        right.addWidget(settings_box)
        right.addWidget(self._info)

        splitter = QSplitter(Qt.Horizontal, self)
        left_widget = QWidget()
        left_widget.setLayout(left)
        right_widget = QWidget()
        right_widget.setLayout(right)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 800])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(splitter)

        self.reload()
        self._sync_buttons()

    # --------------------------------------------------------------- loading
    def reload(self) -> None:
        self._loading = True
        self._list.clear()
        for playlist in self._controller.playlists:
            item = QListWidgetItem(playlist.name)
            item.setData(Qt.UserRole, playlist.id)
            self._list.addItem(item)
        self._loading = False
        active = self._controller.active_playlist
        target = active.id if active else (self._controller.playlists[0].id if self._controller.playlists else "")
        if target:
            self._select_playlist(target)

    def _select_playlist(self, playlist_id: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.UserRole) == playlist_id:
                self._list.setCurrentItem(item)
                return

    def current_playlist(self):
        item = self._list.currentItem()
        if item is None:
            return None
        return self._controller.find_playlist(item.data(Qt.UserRole))

    def _on_playlist_changed(self, *_args) -> None:
        if self._loading:
            return
        self._load_playlist()
        self._sync_buttons()

    def _load_playlist(self) -> None:
        playlist = self.current_playlist()
        self._loading = True
        self._contents.clear()
        if playlist is None:
            self._loading = False
            self._info.setText("")
            return
        for wallpaper_id in playlist.items:
            wallpaper = self._library.get(wallpaper_id)
            if wallpaper is None:
                continue
            entry = QListWidgetItem()
            entry.setData(Qt.UserRole, wallpaper.id)
            entry.setToolTip(wallpaper.path)
            self._contents.addItem(entry)
        self._shuffle.setCurrentIndex(1 if playlist.shuffle else 0)
        index = self._interval.findData(playlist.interval_seconds)
        self._interval.setCurrentIndex(index if index >= 0 else 0)
        self._transition.setCurrentText(
            {"none": "Без перехода", "fade": "Затухание", "slide": "Сдвиг",
             "zoom": "Масштаб", "wipe": "Затёртка"}.get(playlist.transition, "Затухание")
        )
        self._transition_ms.setValue(playlist.transition_ms)
        self._info.setText(
            f"{len(playlist.items)} обоев · "
            + (human_duration(playlist.interval_seconds) if playlist.interval_seconds else "смена вручную")
        )
        self._loading = False

    def _sync_buttons(self) -> None:
        has = self.current_playlist() is not None
        for button in (
            self._rename_btn, self._delete_btn, self._start_btn,
            self._remove_btn, self._clear_btn, self._sort_btn,
            self._up_btn, self._down_btn,
        ):
            button.setEnabled(has)

    # ------------------------------------------------------------- mutations
    def _create_playlist(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый плейлист", "Название:")
        if not ok or not name.strip():
            return
        playlist = self._controller.add_playlist(name.strip())
        self.reload()
        self._select_playlist(playlist.id)
        self.status.emit(f"Создан плейлист «{playlist.name}»")

    def _rename_playlist(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Название:", text=playlist.name)
        if not ok or not name.strip():
            return
        playlist.name = name.strip()
        self._controller.save_playlists()
        self.reload()
        self._select_playlist(playlist.id)

    def _delete_playlist(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        answer = QMessageBox.question(
            self, "Удалить плейлист", f"Удалить плейлист «{playlist.name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._controller.remove_playlist(playlist.id)
        self.reload()
        self.status.emit(f"Плейлист «{playlist.name}» удалён")

    def add_wallpapers(self, wallpapers: list[Wallpaper]) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            self._create_playlist()
            playlist = self.current_playlist()
        if playlist is None:
            return
        existing = set(playlist.items)
        added = 0
        for wallpaper in wallpapers:
            if wallpaper.id not in existing:
                playlist.items.append(wallpaper.id)
                existing.add(wallpaper.id)
                added += 1
        self._controller.save_playlists()
        self._load_playlist()
        self.status.emit(f"Добавлено в «{playlist.name}»: {added}")

    def _save_playlist_settings(self) -> None:
        if self._loading:
            return
        playlist = self.current_playlist()
        if playlist is None:
            return
        playlist.shuffle = bool(self._shuffle.currentData())
        playlist.interval_seconds = int(self._interval.currentData() or 0)
        self._controller.save_playlists()
        self._info.setText(
            f"{len(playlist.items)} обоев · "
            + (human_duration(playlist.interval_seconds) if playlist.interval_seconds else "смена вручную")
        )

    def _on_transition_changed(self) -> None:
        if self._loading:
            return
        playlist = self.current_playlist()
        if playlist is None:
            return
        mapping = {
            "Без перехода": "none", "Затухание": "fade", "Сдвиг": "slide",
            "Масштаб": "zoom", "Затёртка": "wipe",
        }
        playlist.transition = mapping.get(self._transition.currentText(), "fade")
        playlist.transition_ms = self._transition_ms.value()
        self._controller.save_playlists()

    def _move_items(self, delta: int) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        rows = sorted(index.row() for index in self._contents.selectedIndexes())
        if not rows:
            return
        # Map list rows back to playlist entries so missing files never shift
        # positions out of sync with what is stored.
        entry_indices = self._entry_indices(playlist)
        targets = [entry_indices[r] for r in rows if r < len(entry_indices)]
        ordered = list(playlist.items)
        if delta < 0:
            for position in sorted(targets):
                if position > 0:
                    ordered[position - 1], ordered[position] = ordered[position], ordered[position - 1]
        else:
            for position in sorted(targets, reverse=True):
                if position < len(ordered) - 1:
                    ordered[position + 1], ordered[position] = ordered[position], ordered[position + 1]
        playlist.items = ordered
        self._controller.save_playlists()
        self._load_playlist()
        for row in rows:
            if row < self._contents.count():
                self._contents.item(row).setSelected(True)

    def _entry_indices(self, playlist) -> list[int]:
        indices: list[int] = []
        cursor = 0
        for entry_index, wallpaper_id in enumerate(playlist.items):
            if self._library.get(wallpaper_id) is not None:
                indices.append(entry_index)
            cursor += 1
        return indices

    def _remove_selected(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        entry_indices = self._entry_indices(playlist)
        remove_ids = {
            playlist.items[entry_indices[row]]
            for row in sorted(i.row() for i in self._contents.selectedIndexes())
            if row < len(entry_indices)
        }
        playlist.items = [i for i in playlist.items if i not in remove_ids]
        self._controller.save_playlists()
        self._load_playlist()
        self.status.emit(f"Убрано из плейлиста: {len(remove_ids)}")

    def _clear_playlist(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        playlist.items.clear()
        self._controller.save_playlists()
        self._load_playlist()

    def _sort_playlist(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        def key(item_id: str) -> str:
            wallpaper = self._library.get(item_id)
            return (wallpaper.name.lower() if wallpaper else "").zfill(1)
        playlist.items.sort(key=key)
        self._controller.save_playlists()
        self._load_playlist()

    def _on_contents_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        if not self._contents.itemAt(pos):
            return
        menu = QMenu(self)
        menu.addAction("Запустить плейлист", self._start)
        menu.addSeparator()
        menu.addAction("↑ Выше", lambda: self._move_items(-1))
        menu.addAction("↓ Ниже", lambda: self._move_items(1))
        menu.addAction("Сортировать", self._sort_playlist)
        menu.addSeparator()
        menu.addAction("Убрать из плейлиста", self._remove_selected)
        menu.addAction("Очистить плейлист", self._clear_playlist)
        menu.exec(self._contents.viewport().mapToGlobal(pos))

    def _start(self) -> None:
        playlist = self.current_playlist()
        if playlist is None or not playlist.items:
            return
        self._controller.set_playlist(playlist.id)
        self._controller.resume_now()
        self.status.emit(f"Запущен плейлист «{playlist.name}»")
        self.activated.emit()

    def import_from_file(self) -> None:
        playlist = self.current_playlist()
        if playlist is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт списка", str(__import__("pathlib").Path.home()),
            "Текстовый список (*.txt);;Все файлы (*)",
        )
        if not path:
            return
        from pathlib import Path

        added = 0
        seen = set(playlist.items)
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                candidate = Path(line.strip().strip('"'))
                if not candidate.is_file():
                    continue
                wallpaper = self._library.import_file(candidate)
                if wallpaper and wallpaper.id not in seen:
                    playlist.items.append(wallpaper.id)
                    seen.add(wallpaper.id)
                    added += 1
        self._controller.save_playlists()
        self._load_playlist()
        self.status.emit(f"Импортировано: {added}")


__all__ = ["PlaylistsView"]