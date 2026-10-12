"""Main window: sidebar navigation plus the shared player bar."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .. import paths, utils
from ..controller import PlaybackController
from ..hotkeys import DEFAULT_HOTKEYS, HotkeyManager, global_hotkeys_supported
from ..library import Library
from ..models import Wallpaper
from ..version import APP_NAME, APP_VERSION
from . import theme
from .library_view import LibraryView
from .player_bar import PlayerBar
from .playlists_view import PlaylistsView
from .profiles_view import ProfilesView
from .schedule_view import ScheduleView
from .settings_view import SettingsView
from .sources_view import SourcesView
from .tray import Tray, app_icon

log = logging.getLogger(__name__)

PAGES = (
    ("library", "Библиотека"),
    ("sources", "Из интернета"),
    ("playlists", "Плейлисты"),
    ("profiles", "Профили"),
    ("schedule", "Расписание"),
    ("settings", "Настройки"),
)


class MainWindow(QMainWindow):
    """The single window of the app; everything else lives inside it."""

    def __init__(self, config, controller: PlaybackController, player, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._controller = controller
        self._player = player
        self._quitting = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(980, 640)
        self.resize(1240, 780)
        self.setWindowIcon(app_icon(256))

        self._library = Library(config.get("library_dirs", []) or [])

        # --- views ---
        self._library_view = LibraryView(self._library, controller, config, self)
        self._sources_view = SourcesView(self._library, config, self)
        self._playlists_view = PlaylistsView(self._library, controller, config, self)
        self._profiles_view = ProfilesView(self._library, controller, config, self)
        self._schedule_view = ScheduleView(controller, self)
        self._settings_view = SettingsView(config, controller, player, self)

        self._stack = QStackedWidget(self)
        for view in (
            self._library_view, self._sources_view, self._playlists_view,
            self._profiles_view, self._schedule_view, self._settings_view,
        ):
            self._stack.addWidget(view)

        self._nav_buttons = QButtonGroup(self)
        self._nav_buttons.setExclusive(True)
        self._nav = {}
        for key, label in PAGES:
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { text-align: left; padding: 9px 12px; border: none; "
                "border-radius: 7px; background: transparent; }"
                "QPushButton:hover { background: #24262e; }"
                "QPushButton:checked { background: #2d3a5c; color: #ffffff; }"
            )
            self._nav_buttons.addButton(button)
            self._nav[key] = button
            button.clicked.connect(lambda _checked, k=key: self.show_page(k))

        title = QLabel(APP_NAME, self)
        title.setObjectName("SidebarTitle")
        version = QLabel(f"версия {APP_VERSION}", self)
        version.setObjectName("SidebarVersion")

        sidebar = QWidget(self)
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(168)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(8, 0, 8, 12)
        side_layout.setSpacing(3)
        side_layout.addWidget(title)
        side_layout.addWidget(version)
        side_layout.addSpacing(10)
        for key, _label in PAGES:
            side_layout.addWidget(self._nav[key])
        side_layout.addStretch()

        # --- player bar ---
        self._player_bar = PlayerBar(controller, player, self)

        right = QWidget(self)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._stack, 1)
        right_layout.addWidget(self._player_bar)

        container = QWidget(self)
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(sidebar)
        container_layout.addWidget(right, 1)
        self.setCentralWidget(container)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Готово")

        # --- wiring ---
        # The tray is built first: status messages arriving during the wiring
        # below already try to update it.
        self._build_tray()
        self._wire()
        self._build_shortcuts()

        self._nav["library"].setChecked(True)
        self.show_page("library")

    # ------------------------------------------------------------------ wire
    def _wire(self) -> None:
        self._library_view.apply_requested.connect(self._apply_wallpaper)
        self._library_view.preview_requested.connect(self._preview_wallpaper)
        self._library_view.playlist_add_requested.connect(self._add_to_playlist)
        self._library_view.sources_requested.connect(lambda: self.show_page("sources"))
        self._library_view.status.connect(self._say)

        self._sources_view.status.connect(self._say)
        self._sources_view.download_finished.connect(self._on_downloads_finished)

        self._playlists_view.status.connect(self._say)
        self._profiles_view.status.connect(self._say)
        self._profiles_view.applied.connect(lambda: self._library_view.refresh())
        self._schedule_view.status.connect(self._say)
        self._settings_view.status.connect(self._say)
        self._settings_view.settings_changed.connect(self._on_setting_changed)

        self._controller.status_message.connect(self._say)
        self._controller.wallpaper_changed.connect(self._player_bar.set_wallpaper)
        self._controller.pause_changed.connect(self._player_bar.set_paused)
        self._controller.profile_changed.connect(lambda _p: self._player_bar.refresh_profiles())
        self._controller.playlist_changed.connect(self._player_bar.refresh)
        self._player.error.connect(self._say)
        self._player.playback_error.connect(self._on_playback_error)

        self._player_bar.play_pause.connect(self._toggle_playback)
        self._player_bar.next.connect(self._controller.next)
        self._player_bar.previous.connect(self._controller.previous)
        self._player_bar.stop.connect(self._stop_wallpaper)
        self._player_bar.profile_changed.connect(self._activate_profile)

    def _build_tray(self) -> None:
        self._tray = Tray(self, accent=self._accent())
        self._tray.show_requested.connect(self._restore_from_tray)
        self._tray.play_pause_requested.connect(self._toggle_playback)
        self._tray.next_requested.connect(self._controller.next)
        self._tray.previous_requested.connect(self._controller.previous)
        self._tray.open_folders_requested.connect(self._open_library_folder)
        self._tray.quit_requested.connect(self.quit_app)
        if QApplication.instance() is not None:
            try:
                from PySide6.QtWidgets import QSystemTrayIcon

                if QSystemTrayIcon.isSystemTrayAvailable():
                    self._tray.show()
            except Exception:  # noqa: BLE001
                log.debug("Tray unavailable", exc_info=True)
        self._tray.set_playing(not self._controller.paused)

    def _build_shortcuts(self) -> None:
        """In-window shortcuts always work; global ones need the OS handler."""
        local = {
            "Ctrl+Space": self._toggle_playback,
            "Ctrl+Right": self._controller.next,
            "Ctrl+Left": self._controller.previous,
            "Ctrl+Q": self.quit_app,
            "Ctrl+1": lambda: self.show_page("library"),
            "Ctrl+2": lambda: self.show_page("sources"),
            "Ctrl+3": lambda: self.show_page("playlists"),
            "Ctrl+4": lambda: self.show_page("profiles"),
            "Ctrl+5": lambda: self.show_page("schedule"),
            "Ctrl+6": lambda: self.show_page("settings"),
        }
        for sequence, slot in local.items():
            QShortcut(QKeySequence(sequence), self, activated=slot)

        self._hotkeys = HotkeyManager(self)
        QApplication.instance().installNativeEventFilter(self._hotkeys)
        self._register_global_hotkeys()

    def _register_global_hotkeys(self) -> None:
        bindings = {
            "Пауза / воспроизведение": (DEFAULT_HOTKEYS["Пауза / воспроизведение"], self._toggle_playback),
            "Следующие обои": (DEFAULT_HOTKEYS["Следующие обои"], self._controller.next),
            "Предыдущие обои": (DEFAULT_HOTKEYS["Предыдущие обои"], self._controller.previous),
            "Открыть Wallflow": (DEFAULT_HOTKEYS["Открыть Wallflow"], self._restore_from_tray),
            "Показать панель управления": ("", lambda: None),
        }
        win_id = int(self.winId()) if sys.platform == "win32" else 0
        self._hotkeys.register(bindings, win_id)
        supported, reason = global_hotkeys_supported()
        if not supported:
            log.info("Global hotkeys unavailable: %s", reason)
            self._say(f"Глобальные горячие клавиши недоступны: {reason}")

    def _accent(self) -> str:
        value = self._config.get("accent_color", "auto")
        return value if isinstance(value, str) and value.startswith("#") else theme.ACCENT

    # ------------------------------------------------------------ navigation
    def show_page(self, key: str) -> None:
        view = {
            "library": self._library_view,
            "sources": self._sources_view,
            "playlists": self._playlists_view,
            "profiles": self._profiles_view,
            "schedule": self._schedule_view,
            "settings": self._settings_view,
        }.get(key)
        if view is None:
            return
        self._stack.setCurrentWidget(view)
        if key in self._nav:
            self._nav[key].setChecked(True)
        if key == "library":
            self._library_view.refresh()
        elif key == "profiles":
            self._profiles_view.reload()

    # -------------------------------------------------------------- commands
    def _apply_wallpaper(self, wallpaper: Wallpaper) -> None:
        self._controller.set_wallpaper(wallpaper.id)
        self._library_view.refresh()
        self._say(f"Установлены обои «{wallpaper.name}»")

    def _preview_wallpaper(self, wallpaper: Wallpaper) -> None:
        self._player.preview(wallpaper)

    def _add_to_playlist(self, wallpaper: Wallpaper | None) -> None:
        chosen = (
            self._library_view.selected()
            if wallpaper is None
            else [wallpaper]
        )
        if not chosen:
            return
        if not self._controller.playlists:
            self._controller.add_playlist("Мой плейлист")
            self._playlists_view.reload()
        self.show_page("playlists")
        self._playlists_view.add_wallpapers(chosen)

    def _activate_profile(self, profile_id: str) -> None:
        if self._controller.activate_profile(profile_id):
            self._player_bar.refresh_profiles()

    def _toggle_playback(self) -> None:
        self._controller.toggle_pause()
        self._tray.set_playing(not self._controller.paused)

    def _stop_wallpaper(self) -> None:
        self._player.clear()
        self._say("Воспроизведение остановлено")

    def _on_playback_error(self, message: str) -> None:
        self._say(message)
        self._tray.notify(APP_NAME, message)

    def _on_downloads_finished(self) -> None:
        self._library_view.refresh()

    def _on_setting_changed(self, key: str, _value) -> None:
        if key == "thumb_size":
            self._library_view.set_thumb_size(int(self._config.get("thumb_size", 220)))
        elif key == "accent_color":
            theme.apply(QApplication.instance(), self._accent())
        elif key == "static_wallpaper_mode":
            self._controller._apply_now()

    # ------------------------------------------------------------------ misc
    def _say(self, message: str) -> None:
        if message:
            self.statusBar().showMessage(message, 8000)
            self._tray.set_status(message)

    def _open_library_folder(self) -> None:
        target = Path(paths.wallpaper_dir())
        target.mkdir(parents=True, exist_ok=True)
        if utils.which("xdg-open"):
            utils.run(["xdg-open", str(target)], timeout=6)
        elif utils.which("explorer"):
            utils.run(["explorer", str(target)])

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------- lifecycle
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # Give each card a fixed size; QListView works out the column count, so
        # the only thing that matters here is keeping it square-ish.
        self._library_view._view.setGridSize(QSize(230, 284))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._quitting and self._config.get("close_to_tray", True) and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._say("Свернуто в трей")
            return
        event.accept()
        self.quit_app()

    def quit_app(self) -> None:
        self._quitting = True
        self._hotkeys.unregister()
        self._tray.hide()
        QApplication.instance().quit()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._library_view.drop_files(paths)
            event.acceptProposedAction()


__all__ = ["MainWindow"]