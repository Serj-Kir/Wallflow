"""Settings view."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths, utils
from ..version import APP_VERSION
from .widgets import section

log = logging.getLogger(__name__)


class SettingsView(QWidget):
    """Every user-facing preference, grouped into tabs."""

    status = Signal(str)
    settings_changed = Signal(str, object)

    def __init__(self, config, controller, player, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._controller = controller
        self._player = player
        self._loading = False

        tabs = QTabWidget(self)
        tabs.addTab(self._build_general(), "Общие")
        tabs.addTab(self._build_playback(), "Воспроизведение")
        tabs.addTab(self._build_performance(), "Производительность")
        tabs.addTab(self._build_library(), "Библиотека")
        tabs.addTab(self._build_hotkeys(), "Горячие клавиши")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(tabs)

        self.reload()

    # ------------------------------------------------------------- builders
    def _build_general(self) -> QWidget:
        self._start_with_system = QCheckBox("Запускать вместе с системой")
        self._start_with_system.toggled.connect(
            lambda v: self._set("start_with_system", v)
        )
        self._minimize_to_tray = QCheckBox("Сворачивать в трей вместо закрытия")
        self._minimize_to_tray.toggled.connect(
            lambda v: self._set("minimize_to_tray", v)
        )
        self._close_to_tray = QCheckBox("Закрывать окно — сворачивать в трей")
        self._close_to_tray.toggled.connect(lambda v: self._set("close_to_tray", v))

        self._language = QComboBox()
        self._language.addItem("Системный", "system")
        self._language.addItem("Русский", "ru")
        self._language.addItem("English", "en")
        self._language.currentIndexChanged.connect(
            lambda: self._set("language", self._language.currentData())
        )

        self._accent_mode = QComboBox()
        for label, value in (("Автоматически (из обоев)", "auto"), ("Синий (по умолчанию)", "#5b8cff"),
                             ("Фиолетовый", "#8a5bff"), ("Зелёный", "#3fb27f"),
                             ("Оранжевый", "#e0803f"), ("Не менять", "none")):
            self._accent_mode.addItem(label, value)
        self._accent_mode.currentIndexChanged.connect(
            lambda: self._set("accent_color", self._accent_mode.currentData())
        )

        self._hide_icons = QCheckBox("Скрывать значки рабочего стола, пока играет анимация")
        self._hide_icons.toggled.connect(lambda v: self._set("hide_desktop_icons", v))

        system_box, system_layout = section("Система")
        system_layout.addWidget(self._start_with_system)
        system_layout.addWidget(self._minimize_to_tray)
        system_layout.addWidget(self._close_to_tray)

        look_box, look_layout = section("Оформление")
        look_layout.addWidget(QLabel("Язык интерфейса"))
        look_layout.addWidget(self._language)
        look_layout.addWidget(QLabel("Цвет интерфейса"))
        look_layout.addWidget(self._accent_mode)

        desktop_box, desktop_layout = section("Рабочий стол")
        desktop_layout.addWidget(self._hide_icons)
        warning = QLabel(
            "Скрытие значков зависит от рабочего стола и требует перезапуска оболочки "
            "на некоторых системах."
        )
        warning.setObjectName("Hint")
        warning.setWordWrap(True)
        desktop_layout.addWidget(warning)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(system_box)
        layout.addWidget(look_box)
        layout.addWidget(desktop_box)
        layout.addStretch()
        return page

    def _build_playback(self) -> QWidget:
        self._pause_fullscreen = QCheckBox("Пауза в полноэкранных приложениях и играх")
        self._pause_fullscreen.toggled.connect(lambda v: self._set("pause_on_fullscreen", v))
        self._pause_lock = QCheckBox("Пауза при блокировке экрана")
        self._pause_lock.toggled.connect(lambda v: self._set("pause_on_lock", v))
        self._pause_battery = QCheckBox("Пауза при работе от батареи")
        self._pause_battery.toggled.connect(lambda v: self._set("pause_on_battery", v))
        self._battery_threshold = QSpinBox()
        self._battery_threshold.setRange(0, 100)
        self._battery_threshold.setSuffix(" %")
        self._battery_threshold.setEnabled(False)
        self._battery_threshold.valueChanged.connect(
            lambda v: self._set("battery_pause_threshold", v)
        )
        self._pause_battery.toggled.connect(self._battery_threshold.setEnabled)
        self._pause_remote = QCheckBox("Пауза в удалённом сеансе (RDP/SSH)")
        self._pause_remote.toggled.connect(lambda v: self._set("pause_on_remote_session", v))
        self._pause_idle = QCheckBox("Пауза при простое")
        self._pause_idle.toggled.connect(lambda v: self._set("pause_when_idle", v))
        self._idle_minutes = QSpinBox()
        self._idle_minutes.setRange(1, 120)
        self._idle_minutes.setSuffix(" мин")
        self._idle_minutes.setEnabled(False)
        self._idle_minutes.valueChanged.connect(lambda v: self._set("idle_minutes", v))
        self._pause_idle.toggled.connect(self._idle_minutes.setEnabled)

        self._static_mode = QComboBox()
        for label, value in (
            ("Автоматически (рекомендуется)", "auto"),
            ("Через систему — чётче", "native"),
            ("Своим окном — больше возможностей", "window"),
        ):
            self._static_mode.addItem(label, value)
        self._static_mode.currentIndexChanged.connect(
            lambda: self._set("static_wallpaper_mode", self._static_mode.currentData())
        )
        self._interactive_web = QCheckBox("HTML-обои реагируют на клики мышью")
        self._interactive_web.toggled.connect(lambda v: self._set("interactive_web_wallpapers", v))
        self._loop_videos = QCheckBox("Повторять видео бесконечно")
        self._loop_videos.setToolTip(
            "Если выключить, видео проиграется один раз и остановится"
        )
        self._loop_videos.toggled.connect(self._on_video_setting_changed)
        self._video_audio = QCheckBox("Звук в видео-обоях")
        self._video_audio.setToolTip("Обычно обои идут без звука — так их не слышно")
        self._video_audio.toggled.connect(self._on_video_setting_changed)

        pause_box, pause_layout = section("Автоматическая пауза")
        for widget in (self._pause_fullscreen, self._pause_lock, self._pause_remote, self._pause_idle):
            pause_layout.addWidget(widget)
        battery_row = QHBoxLayout()
        battery_row.addWidget(self._pause_battery)
        battery_row.addSpacing(14)
        battery_row.addWidget(QLabel("не ниже"))
        battery_row.addWidget(self._battery_threshold)
        battery_row.addStretch()
        pause_layout.addLayout(battery_row)
        idle_row = QHBoxLayout()
        idle_row.addWidget(QLabel("    простой дольше"))
        idle_row.addWidget(self._idle_minutes)
        idle_row.addStretch()
        pause_layout.addLayout(idle_row)

        mode_box, mode_layout = section("Как показывать статичные фото")
        mode_layout.addWidget(self._static_mode)
        mode_hint = QLabel(
            "«Через систему» отдаёт фото рабочему столу — это самое чёткое качество и "
            "правильное поведение на Wayland. «Своим окном» нужно для собственных "
            "переходов и обоев на каждом мониторе там, где система не умеет."
        )
        mode_hint.setObjectName("Hint")
        mode_hint.setWordWrap(True)
        mode_layout.addWidget(mode_hint)
        mode_layout.addWidget(self._interactive_web)

        video_box, video_layout = section("Видео")
        video_layout.addWidget(self._loop_videos)
        video_layout.addWidget(self._video_audio)
        video_hint = QLabel(
            "Сцены и HTML-обои повторяются всегда — они зациклены по своей природе."
        )
        video_hint.setObjectName("Hint")
        video_hint.setWordWrap(True)
        video_layout.addWidget(video_hint)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(pause_box)
        layout.addWidget(mode_box)
        layout.addWidget(video_box)
        layout.addStretch()
        return page

    def _build_performance(self) -> QWidget:
        self._max_fps = QSpinBox()
        self._max_fps.setRange(5, 120)
        self._max_fps.setSuffix(" fps")
        self._max_fps.valueChanged.connect(self._on_fps_changed)

        self._memory_limit = QSpinBox()
        self._memory_limit.setRange(128, 8192)
        self._memory_limit.setSingleStep(128)
        self._memory_limit.setSuffix(" МБ")
        self._memory_limit.valueChanged.connect(lambda v: self._set("memory_limit_mb", v))

        self._hw_accel = QCheckBox("Аппаратное ускорение для HTML-обоев")
        self._hw_accel.toggled.connect(lambda v: self._set("hardware_acceleration", v))
        self._battery_saver = QCheckBox("Отключать анимацию в режиме энергосбережения")
        self._battery_saver.toggled.connect(lambda v: self._set("disable_battery_saver_live", v))

        self._status = QLabel("", self)
        self._status.setObjectName("Hint")
        self._status.setWordWrap(True)

        perf_box, perf_layout = section("Ресурсы")
        perf_layout.addWidget(QLabel("Максимум кадров в секунду для анимаций"))
        perf_layout.addWidget(self._max_fps)
        perf_layout.addWidget(QLabel("Лимит памяти на воспроизведение"))
        perf_layout.addWidget(self._memory_limit)
        perf_layout.addWidget(self._hw_accel)
        perf_layout.addWidget(self._battery_saver)

        about_box, about_layout = section("О программе")
        about_layout.addWidget(QLabel(f"{APP_VERSION} · MIT License"))
        buttons = QHBoxLayout()
        open_logs = QPushButton("Открыть папку с логами")
        open_logs.clicked.connect(lambda: self._open_path(paths.log_dir()))
        open_data = QPushButton("Открыть папку данных")
        open_data.clicked.connect(lambda: self._open_path(paths.data_dir()))
        reset = QPushButton("Сбросить настройки")
        reset.setObjectName("Danger")
        reset.clicked.connect(self._reset)
        buttons.addWidget(open_logs)
        buttons.addWidget(open_data)
        buttons.addStretch()
        buttons.addWidget(reset)
        about_layout.addLayout(buttons)
        about_layout.addWidget(self._status)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(perf_box)
        layout.addWidget(about_box)
        layout.addStretch()
        return page

    def _build_library(self) -> QWidget:
        self._library_list = QListWidget(self)
        self._library_list.setSelectionMode(QListWidget.NoSelection)

        add = QPushButton("Добавить папку…")
        add.clicked.connect(self._add_library_dir)
        remove = QPushButton("Убрать")
        remove.clicked.connect(self._remove_library_dir)

        self._thumb_size = QComboBox()
        for value in (140, 180, 220, 260, 320):
            self._thumb_size.addItem(f"{value} px", value)
        self._thumb_size.currentIndexChanged.connect(self._on_thumb_size)

        self._watch = QCheckBox("Следить за папками и подхватывать новые файлы")
        self._watch.toggled.connect(lambda v: self._set("watch_library", v))

        self._download_folder = QLineEdit(self)
        self._download_folder.setReadOnly(True)
        choose = QPushButton("Выбрать…")
        choose.clicked.connect(self._choose_download_folder)
        reset_dir = QPushButton("По умолчанию")
        reset_dir.clicked.connect(self._reset_download_folder)

        folders_box, folders_layout = section("Папки библиотеки")
        folders_layout.addWidget(self._library_list)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        folders_layout.addLayout(row)
        folders_layout.addWidget(self._watch)

        display_box, display_layout = section("Отображение")
        display_layout.addWidget(QLabel("Размер превью"))
        display_layout.addWidget(self._thumb_size)

        download_box, download_layout = section("Загрузки")
        download_layout.addWidget(QLabel("Куда сохранять скачанное"))
        download_row = QHBoxLayout()
        download_row.addWidget(self._download_folder, 1)
        download_row.addWidget(choose)
        download_row.addWidget(reset_dir)
        download_layout.addLayout(download_row)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(folders_box)
        layout.addWidget(display_box)
        layout.addWidget(download_box)
        layout.addStretch()
        return page

    def _build_hotkeys(self) -> QWidget:
        box, box_layout = section("Горячие клавиши")
        self._hotkey_table = QListWidget(self)
        self._hotkey_table.setMaximumHeight(320)
        box_layout.addWidget(self._hotkey_table)

        note = QLabel(
            "Горячие клавиши работают, пока Wallflow запущен. "
            "Регистрация выполняется системой; если клавиша уже занята другим "
            "приложением, Wallflow сообщит об этом в журнал."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)
        box_layout.addWidget(note)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(box)
        layout.addStretch()
        return page

    # ----------------------------------------------------------------- state
    def _set(self, key: str, value: Any) -> None:
        if self._loading:
            return
        self._config.set(key, value)
        self.settings_changed.emit(key, value)
        self.status.emit(f"Сохранено: {key}")

    def reload(self) -> None:
        self._loading = True
        cfg = self._config
        self._start_with_system.setChecked(bool(cfg.get("start_with_system")))
        self._minimize_to_tray.setChecked(bool(cfg.get("minimize_to_tray")))
        self._close_to_tray.setChecked(bool(cfg.get("close_to_tray")))
        self._language.setCurrentIndex(max(0, self._language.findData(cfg.get("language", "system"))))
        self._accent_mode.setCurrentIndex(
            max(0, self._accent_mode.findData(cfg.get("accent_color", "auto")))
        )
        self._hide_icons.setChecked(bool(cfg.get("hide_desktop_icons")))

        self._pause_fullscreen.setChecked(bool(cfg.get("pause_on_fullscreen")))
        self._pause_lock.setChecked(bool(cfg.get("pause_on_lock")))
        self._pause_battery.setChecked(bool(cfg.get("pause_on_battery")))
        self._battery_threshold.setValue(int(cfg.get("battery_pause_threshold", 20)))
        self._battery_threshold.setEnabled(self._pause_battery.isChecked())
        self._pause_remote.setChecked(bool(cfg.get("pause_on_remote_session")))
        self._pause_idle.setChecked(bool(cfg.get("pause_when_idle")))
        self._idle_minutes.setValue(int(cfg.get("idle_minutes", 5)))
        self._idle_minutes.setEnabled(self._pause_idle.isChecked())
        self._static_mode.setCurrentIndex(
            max(0, self._static_mode.findData(cfg.get("static_wallpaper_mode", "auto")))
        )
        self._interactive_web.setChecked(bool(cfg.get("interactive_web_wallpapers", True)))
        self._loop_videos.setChecked(bool(cfg.get("loop_videos", True)))
        self._video_audio.setChecked(bool(cfg.get("video_audio", False)))

        self._max_fps.setValue(int(cfg.get("max_fps", 30)))
        self._memory_limit.setValue(int(cfg.get("memory_limit_mb", 1024)))
        self._hw_accel.setChecked(bool(cfg.get("hardware_acceleration")))
        self._battery_saver.setChecked(bool(cfg.get("disable_battery_saver_live")))
        self._watch.setChecked(bool(cfg.get("watch_library")))
        self._thumb_size.setCurrentIndex(max(0, self._thumb_size.findData(int(cfg.get("thumb_size", 220)))))
        self._download_folder.setText(cfg.get("download_folder") or str(paths.wallpaper_dir()))

        self._reload_library_dirs()
        self._reload_hotkeys()
        self._reload_status()
        self._loading = False

    def _reload_status(self) -> None:
        from ..live import capabilities

        caps = capabilities()
        missing = [name for name, ok in caps.items() if not ok]
        if missing:
            names = {"video": "видео", "web": "HTML"}
            text = "Недоступные форматы: " + ", ".join(names.get(m, m) for m in missing)
            text += ". Установите PySide6-Addons для полной поддержки."
        else:
            text = "Все форматы доступны: фото, видео, HTML и сцены."
        text += f"\nМониторов подключено: {len(self._player.monitors())}"
        self._status.setText(text)

    def _reload_library_dirs(self) -> None:
        self._library_list.clear()
        self._library_list.addItem(str(paths.wallpaper_dir()))
        for entry in self._config.get("library_dirs", []) or []:
            self._library_list.addItem(str(entry))

    def _reload_hotkeys(self) -> None:
        self._hotkey_table.clear()
        from ..hotkeys import DEFAULT_HOTKEYS

        for action, combo in DEFAULT_HOTKEYS.items():
            item = QListWidgetItem(f"{action}")
            item.setText(f"{action:<34}{combo if combo else '— не задано —'}")
            self._hotkey_table.addItem(item)

    # --------------------------------------------------------------- actions
    def _add_library_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка с обоями", str(Path.home()))
        if not folder:
            return
        dirs = list(self._config.get("library_dirs", []) or [])
        if folder not in dirs:
            dirs.append(folder)
            self._config.set("library_dirs", dirs)
        self._library_list.addItem(folder)
        self.status.emit(f"Добавлена папка: {folder}")

    def _remove_library_dir(self) -> None:
        item = self._library_list.currentItem()
        if item is None:
            return
        folder = item.text()
        dirs = [d for d in (self._config.get("library_dirs", []) or []) if d != folder]
        self._config.set("library_dirs", dirs)
        self._reload_library_dirs()

    def _choose_download_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Папка для загрузок", self._download_folder.text() or str(Path.home())
        )
        if folder:
            self._config.set("download_folder", folder)
            self._download_folder.setText(folder)

    def _reset_download_folder(self) -> None:
        self._config.set("download_folder", "")
        self._download_folder.setText(str(paths.wallpaper_dir()))

    def _on_thumb_size(self) -> None:
        self._set("thumb_size", int(self._thumb_size.currentData()))

    def _on_video_setting_changed(self) -> None:
        """Save both video toggles and tell the player to apply them at once."""
        if self._loading:
            return
        self._config.update(
            {
                "loop_videos": self._loop_videos.isChecked(),
                "video_audio": self._video_audio.isChecked(),
            }
        )
        self.settings_changed.emit("loop_videos", self._loop_videos.isChecked())
        self.status.emit("Настройки видео сохранены")

    def _on_fps_changed(self, value: int) -> None:
        self._set("max_fps", value)
        self._player.set_max_fps(value)

    def _open_path(self, path: Path) -> None:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        if utils.which("xdg-open"):
            utils.run(["xdg-open", str(target)], timeout=6)
        elif utils.which("explorer"):
            utils.run(["explorer", str(target)])

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self, "Сбросить настройки",
            "Все настройки вернутся к значениям по умолчанию.\n"
            "Библиотека обоев и профили останутся на месте.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._config.reset_defaults()
        self.reload()
        self.status.emit("Настройки сброшены")


__all__ = ["SettingsView"]