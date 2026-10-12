"""Profiles view: switch wallpaper setups and assign wallpapers per monitor."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..models import MonitorInfo, Wallpaper
from ..utils import format_resolution
from .widgets import ThumbnailCache, WallpaperDelegate, section

log = logging.getLogger(__name__)


class MonitorCard(QGroupBox):
    """One monitor and the wallpaper assigned to it."""

    changed = Signal()

    def __init__(self, info: MonitorInfo, cache: ThumbnailCache, parent=None) -> None:
        super().__init__(self._title_for(info), parent)
        self._info = info
        self._cache = cache
        self._model: list[Wallpaper] = []

        self._picker = QListWidget(self)
        self._picker.setViewMode(QListWidget.IconMode)
        self._picker.setResizeMode(QListWidget.Adjust)
        self._picker.setMovement(QListWidget.Static)
        self._picker.setSelectionMode(QListWidget.SingleSelection)
        self._picker.setWordWrap(True)
        self._picker.setMinimumHeight(150)
        self._delegate = WallpaperDelegate(cache, self._picker)
        self._delegate.double_clicked.connect(lambda w: self._choose(w))
        self._picker.setItemDelegate(self._delegate)
        self._picker.itemDoubleClicked.connect(lambda _i: self._apply_picker())

        self._fit = QComboBox(self)
        for label, value in (("Заполнить", "fill"), ("Вписать", "fit"), ("Растянуть", "stretch"),
                             ("По центру", "center"), ("Плиткой", "tile")):
            self._fit.addItem(label, value)
        self._fit.currentIndexChanged.connect(lambda _i: self.changed.emit())

        clear = QPushButton("Следовать основному", self)
        clear.clicked.connect(self._clear)

        layout = QVBoxLayout(self)
        layout.addWidget(self._picker)
        row = QHBoxLayout()
        row.addWidget(QLabel("Масштаб:"))
        row.addWidget(self._fit)
        row.addStretch()
        row.addWidget(clear)
        layout.addLayout(row)

    @staticmethod
    def _title_for(info: MonitorInfo) -> str:
        bits = [info.name or info.id]
        if info.width and info.height:
            bits.append(f"{format_resolution(info.width, info.height)}")
        if info.primary:
            bits.append("основной")
        return " · ".join(bits)

    @property
    def monitor_id(self) -> str:
        return self._info.id

    def set_items(self, wallpapers: list[Wallpaper]) -> None:
        self._model = wallpapers
        self._picker.clear()
        for wallpaper in wallpapers[:200]:  # enough to choose from without stalling
            entry = QListWidgetItem()
            entry.setData(Qt.UserRole, wallpaper.id)
            entry.setToolTip(wallpaper.name)
            self._picker.addItem(entry)

    def selected_id(self) -> Optional[str]:
        item = self._picker.currentItem()
        return item.data(Qt.UserRole) if item else None

    def select(self, wallpaper_id: Optional[str]) -> None:
        if not wallpaper_id:
            self._picker.clearSelection()
            return
        for row in range(self._picker.count()):
            if self._picker.item(row).data(Qt.UserRole) == wallpaper_id:
                self._picker.selectRow(row)
                return

    def _choose(self, wallpaper: Wallpaper) -> None:
        self.select(wallpaper.id)
        self._apply_picker()

    def _apply_picker(self) -> None:
        self.changed.emit()

    def _clear(self) -> None:
        self._picker.clearSelection()
        self.changed.emit()


class ProfilesView(QWidget):
    """Create profiles, pick their wallpaper/playlist and per-monitor setup."""

    status = Signal(str)
    applied = Signal()

    def __init__(self, library, controller, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._controller = controller
        self._config = config
        self._cache = ThumbnailCache(170)
        self._monitor_cards: list[MonitorCard] = []

        self._list = QListWidget(self)
        self._list.setMaximumWidth(200)
        self._list.currentItemChanged.connect(self._on_profile_changed)

        self._new_btn = QPushButton("+ Создать", self)
        self._new_btn.clicked.connect(self._create)
        self._rename_btn = QPushButton("Переименовать", self)
        self._rename_btn.clicked.connect(self._rename)
        self._delete_btn = QPushButton("Удалить", self)
        self._delete_btn.setObjectName("Danger")
        self._delete_btn.clicked.connect(self._delete)
        self._activate_btn = QPushButton("Применить", self)
        self._activate_btn.setObjectName("Primary")
        self._activate_btn.clicked.connect(self._activate)
        self._duplicate_btn = QPushButton("Дублировать", self)
        self._duplicate_btn.clicked.connect(self._duplicate)
        self._export_btn = QPushButton("Сохранить в файл…", self)
        self._export_btn.clicked.connect(self._export)
        self._import_btn = QPushButton("Загрузить из файла…", self)
        self._import_btn.clicked.connect(self._import)

        self._name = QLineEdit(self)
        self._name.editingFinished.connect(self._on_rename_editing)

        self._mode = QComboBox(self)
        self._mode.addItem("Обои", "wallpaper")
        self._mode.addItem("Плейлист", "playlist")
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        self._target = QComboBox(self)
        self._target.currentIndexChanged.connect(self._on_target_changed)

        self._startup_check = QComboBox(self)
        self._startup_check.addItem("Нет", "")
        for profile in self._controller.profiles:
            self._startup_check.addItem(profile.name, profile.id)
        self._startup_check.currentIndexChanged.connect(self._on_startup_changed)

        self._accent = QComboBox(self)
        self._accent.addItem("Автоматически (из обоев)", "auto")
        self._accent.addItem("Не менять", "none")
        self._accent.currentIndexChanged.connect(self._on_accent_changed)

        left = QVBoxLayout()
        left.addWidget(self._list)
        left.addWidget(self._activate_btn)
        left.addWidget(self._new_btn)
        left.addWidget(self._duplicate_btn)
        left.addWidget(self._rename_btn)
        left.addWidget(self._delete_btn)

        basics_box, basics_layout = section("Основное")
        form = QFormLayout()
        form.addRow("Название:", self._name)
        form.addRow("Режим:", self._mode)
        form.addRow("Что показывать:", self._target)
        basics_layout.addLayout(form)

        general_box, general_layout = section("Общее")
        general_layout.addWidget(QLabel("Профиль при запуске"))
        general_layout.addWidget(self._startup_check)
        general_layout.addWidget(QLabel("Цвет интерфейса"))
        general_layout.addWidget(self._accent)
        hint = QLabel("Запускается только при автозапуске Wallflow вместе с системой.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        general_layout.addWidget(hint)

        self._monitors_layout = QVBoxLayout()
        monitors_box, monitors_inner = section("Мониторы")
        monitors_inner.addLayout(self._monitors_layout)
        note = QLabel(
            "Если монитор не выбран, он показывает основные обои профиля. "
            "Поддержка «обои на каждый монитор» зависит от системы."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        monitors_inner.addWidget(note)

        right = QVBoxLayout()
        right.addWidget(basics_box)
        right.addWidget(general_box)
        right.addWidget(monitors_box)
        right.addStretch()

        row = QHBoxLayout()
        row.addLayout(left)
        row.addLayout(right, 1)

        bottom = QHBoxLayout()
        bottom.addWidget(self._export_btn)
        bottom.addWidget(self._import_btn)
        bottom.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(row, 1)
        layout.addLayout(bottom)

        self.reload()

    # ---------------------------------------------------------------- reload
    def reload(self) -> None:
        self._loading = True
        self._list.clear()
        active_id = self._controller.active_profile.id if self._controller.active_profile else None
        for profile in self._controller.profiles:
            item = QListWidgetItem(profile.name)
            item.setData(Qt.UserRole, profile.id)
            if profile.id == active_id:
                font = item.font()
                from PySide6.QtGui import QFont

                font.setBold(True)
                item.setFont(font)
            self._list.addItem(item)
        self._rebuild_targets()
        self._rebuild_startup()
        self._rebuild_monitors()
        self._loading = False
        if active_id:
            self._select(active_id)
        elif self._list.count():
            self._list.setCurrentRow(0)

    def _select(self, profile_id: str) -> None:
        for row in range(self._list.count()):
            if self._list.item(row).data(Qt.UserRole) == profile_id:
                self._list.setCurrentRow(row)
                return

    def current_profile(self):
        item = self._list.currentItem()
        if item is None:
            return None
        return self._controller.find_profile(item.data(Qt.UserRole))

    def _on_profile_changed(self, *_args) -> None:
        if getattr(self, "_loading", False):
            return
        self._sync_form()

    def _sync_form(self) -> None:
        profile = self.current_profile()
        has = profile is not None
        for widget in (self._name, self._mode, self._target, self._activate_btn,
                       self._rename_btn, self._delete_btn, self._duplicate_btn,
                       self._export_btn):
            widget.setEnabled(has)
        if not has:
            return
        self._name.setText(profile.name)
        mode = "playlist" if profile.playlist_id else "wallpaper"
        self._mode.setCurrentIndex(self._mode.findData(mode))
        self._fill_targets(profile, mode)
        self._accent.setCurrentIndex(
            self._accent.findData(profile.accent_color or "auto")
        )
        monitor_map = profile.monitor_map()
        for card in self._monitor_cards:
            card.select(monitor_map.get(card.monitor_id))
        self._delete_btn.setEnabled(not profile.locked and len(self._controller.profiles) > 1)
        self._name.setReadOnly(profile.locked)

    def _fill_targets(self, profile, mode: str) -> None:
        self._target.blockSignals(True)
        self._target.clear()
        self._target.addItem("— ничего —", "")
        if profile is None:
            self._target.blockSignals(False)
            return
        if mode == "playlist":
            for playlist in self._controller.playlists:
                self._target.addItem(f"{playlist.name} ({len(playlist.items)})", playlist.id)
            index = self._target.findData(profile.playlist_id)
            self._target.setCurrentIndex(max(0, index))
        else:
            current_id = profile.wallpaper_id
            ordered = sorted(self._library.all(), key=lambda w: w.name.lower())
            for wallpaper in ordered:
                self._target.addItem(wallpaper.name, wallpaper.id)
                if wallpaper.id == current_id:
                    self._target.setCurrentIndex(self._target.count() - 1)
        self._target.blockSignals(False)

    def _rebuild_targets(self) -> None:
        profile = self.current_profile()
        mode = self._mode.currentData() or "wallpaper"
        self._fill_targets(profile, mode if profile else "wallpaper")

    def _rebuild_startup(self) -> None:
        current = self._config.get("startup_profile_id", "")
        self._startup_check.blockSignals(True)
        self._startup_check.clear()
        self._startup_check.addItem("Нет", "")
        for profile in self._controller.profiles:
            self._startup_check.addItem(profile.name, profile.id)
        index = self._startup_check.findData(current)
        self._startup_check.setCurrentIndex(index if index >= 0 else 0)
        self._startup_check.blockSignals(False)

    def _rebuild_monitors(self) -> None:
        while self._monitors_layout.count():
            item = self._monitors_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._monitor_cards.clear()

        wallpapers = sorted(self._library.all(), key=lambda w: w.name.lower())[:200]
        for monitor in self._controller.monitors():
            card = MonitorCard(monitor, self._cache, self)
            card.set_items(wallpapers)
            card.changed.connect(self._on_monitor_changed)
            self._monitors_layout.addWidget(card)
            self._monitor_cards.append(card)
        profile = self.current_profile()
        if profile:
            monitor_map = profile.monitor_map()
            for card in self._monitor_cards:
                card.select(monitor_map.get(card.monitor_id))
        self._monitors_layout.addStretch()

    # --------------------------------------------------------------- editing
    def _create(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый профиль", "Название:", text="Профиль")
        if not ok or not name.strip():
            return
        profile = self._controller.add_profile(name.strip())
        self.reload()
        self._select(profile.id)
        self.status.emit(f"Создан профиль «{profile.name}»")

    def _duplicate(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        copy = self._controller.add_profile(f"{profile.name} (копия)", wallpaper_id=profile.wallpaper_id)
        copy.playlist_id = profile.playlist_id
        copy.monitors = [type(m)(m.monitor_id, m.wallpaper_id, m.fit, m.alignment) for m in profile.monitors]
        copy.accent_color = profile.accent_color
        self._controller.save_profiles()
        self.reload()
        self._select(copy.id)

    def _rename(self) -> None:
        self._list.setFocus()
        self._name.setFocus()
        self._name.selectAll()

    def _on_rename_editing(self) -> None:
        profile = self.current_profile()
        if profile is None or profile.locked:
            return
        name = self._name.text().strip()
        if not name or name == profile.name:
            self._name.setText(profile.name)
            return
        profile.name = name
        self._controller.save_profiles()
        self.reload()
        self._select(profile.id)

    def _delete(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        answer = QMessageBox.question(
            self, "Удалить профиль", f"Удалить профиль «{profile.name}»?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self._controller.remove_profile(profile.id):
            self.reload()
            self.status.emit(f"Профиль «{profile.name}» удалён")

    def _on_mode_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        profile = self.current_profile()
        if profile is None:
            return
        if self._mode.currentData() == "playlist":
            profile.playlist_id = profile.playlist_id or (self._controller.playlists[0].id if self._controller.playlists else "")
            profile.wallpaper_id = None
        else:
            profile.playlist_id = None
            if not profile.wallpaper_id and len(self._library):
                profile.wallpaper_id = self._library.all()[0].id
        self._controller.save_profiles()
        self._fill_targets(profile, self._mode.currentData())

    def _on_target_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        profile = self.current_profile()
        if profile is None:
            return
        value = self._target.currentData() or None
        if self._mode.currentData() == "playlist":
            profile.playlist_id = value
        else:
            profile.wallpaper_id = value
        self._controller.save_profiles()

    def _on_startup_changed(self) -> None:
        self._config.set("startup_profile_id", self._startup_check.currentData() or "")

    def _on_accent_changed(self) -> None:
        if getattr(self, "_loading", False):
            return
        profile = self.current_profile()
        if profile is None:
            return
        value = self._accent.currentData()
        profile.accent_color = "" if value == "auto" else value
        self._controller.save_profiles()

    def _on_monitor_changed(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        for card in self._monitor_cards:
            profile.set_monitor(card.monitor_id, card.selected_id())
        self._controller.save_profiles()
        self._controller._apply_now()
        self.status.emit("Настройка мониторов обновлена")

    def _activate(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        self._controller.activate_profile(profile.id)
        self.reload()
        self.status.emit(f"Применён профиль «{profile.name}»")
        self.applied.emit()

    # -------------------------------------------------------- import/export
    def _export(self) -> None:
        profile = self.current_profile()
        if profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить профиль", f"{profile.name}.wallflow-profile", "Профиль Wallflow (*.wallflow-profile)"
        )
        if not path:
            return
        import json

        payload = {
            "version": 1,
            "profile": profile.to_dict(),
            "items": [
                {"id": item_id, "path": (self._library.get(item_id).path if self._library.get(item_id) else "")}
                for item_id in _collect_items(profile, self._controller)
            ],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        self.status.emit(f"Профиль сохранён: {path}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Загрузить профиль", str(__import__("pathlib").Path.home()),
            "Профиль Wallflow (*.wallflow-profile);;Все файлы (*)",
        )
        if not path:
            return
        import json
        from pathlib import Path

        from ..models import MonitorAssignment, Profile

        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Ошибка", f"Не удалось прочитать профиль:\n{exc}")
            return

        raw_profile = payload.get("profile") or {}
        profile = Profile.from_dict(raw_profile)
        # Files may live on a different machine; re-import them from disk.
        remapped: dict[str, str] = {}
        for entry in payload.get("items", []):
            original_id, original_path = entry.get("id"), entry.get("path")
            if not original_path:
                continue
            source = Path(original_path)
            if not source.exists():
                continue
            imported = self._library.import_file(source)
            if imported:
                remapped[original_id] = imported.id
        if remapped:
            profile.wallpaper_id = remapped.get(profile.wallpaper_id, profile.wallpaper_id)
            for monitor in profile.monitors:
                monitor.wallpaper_id = remapped.get(monitor.wallpaper_id or "", monitor.wallpaper_id)

        self._controller.profiles.append(profile)
        self._controller.save_profiles()
        self.reload()
        self._select(profile.id)
        self.status.emit(f"Профиль загружен: {profile.name}")


def _collect_items(profile, controller) -> list[str]:
    ids: list[str] = [profile.wallpaper_id] if profile.wallpaper_id else []
    ids += [m.wallpaper_id for m in profile.monitors if m.wallpaper_id]
    playlist = controller.find_playlist(profile.playlist_id) if profile.playlist_id else None
    if playlist:
        ids += playlist.items
    return [i for i in ids if i]


__all__ = ["ProfilesView", "MonitorCard"]