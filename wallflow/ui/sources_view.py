"""Sources view: search free wallpaper sites and download into the library."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths, sources
from ..models import Wallpaper
from ..utils import unique_path
from .widgets import EmptyState, ThumbnailCache

log = logging.getLogger(__name__)


class _SearchTask(QRunnable):
    """Runs a provider search off the UI thread."""

    def __init__(self, provider: sources.Provider, query: str, page: int, per_page: int) -> None:
        super().__init__()
        self._provider = provider
        self._query = query
        self._page = page
        self._per_page = per_page
        self.signals = _SearchSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102
        try:
            results = self._provider.search(self._query, self._page, self._per_page)
            self.signals.finished.emit(self._provider.name, results, "")
        except sources.ProviderError as exc:
            self.signals.finished.emit(self._provider.name, [], str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("Provider search crashed")
            self.signals.finished.emit(self._provider.name, [], str(exc))


class _SearchSignals(QObject):
    finished = Signal(str, list, str)


class _DownloadTask(QRunnable):
    """Downloads one image and reports progress."""

    def __init__(self, result: sources.SearchResult, destination: Path) -> None:
        super().__init__()
        self._result = result
        self._destination = destination
        self.signals = _DownloadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D102
        try:
            path = sources.download(
                self._result, self._destination,
                progress=lambda read, total: self.signals.progress.emit(read, total),
            )
            self.signals.finished.emit(self._result, path, "")
        except Exception as exc:  # noqa: BLE001
            self.signals.finished.emit(self._result, None, str(exc))


class _DownloadSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(object, object, str)


class SourcesView(QWidget):
    """Search providers, browse results, download favourites."""

    status = Signal(str)
    download_finished = Signal()

    def __init__(self, library, config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library
        self._config = config
        self._cache = ThumbnailCache(180)
        self._results: list[sources.SearchResult] = []
        self._thread_pool: list[QRunnable] = []
        self._active_downloads = 0

        self._provider_box = QComboBox(self)
        self._api_keys: dict[str, str] = dict(config.get("source_api_keys", {}) or {})
        self._reload_providers()

        self._query = QLineEdit(self)
        self._query.setPlaceholderText("Что найти? Например: горы, космос, abstract…")
        self._query.returnPressed.connect(self._search)

        self._search_btn = QPushButton("Найти", self)
        self._search_btn.setObjectName("Primary")
        self._search_btn.clicked.connect(self._search)

        self._daily_btn = QPushButton("Фото дня", self)
        self._daily_btn.clicked.connect(self._load_daily)

        self._grid = QListWidget(self)
        self._grid.setViewMode(QListWidget.IconMode)
        self._grid.setResizeMode(QListWidget.Adjust)
        self._grid.setMovement(QListWidget.Static)
        self._grid.setSelectionMode(QListWidget.ExtendedSelection)
        self._grid.setWordWrap(True)
        self._grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self._grid.customContextMenuRequested.connect(self._on_context_menu)
        self._grid.itemSelectionChanged.connect(self._update_download_button)
        self._grid.setAcceptDrops(True)
        self._grid.viewport().installEventFilter(self)

        self._progress = QProgressBar(self)
        self._progress.setVisible(False)
        self._progress.setFormat("%p%")

        self._download_btn = QPushButton("Скачать выбранное", self)
        self._download_btn.setObjectName("Primary")
        self._download_btn.clicked.connect(self._download_selected)
        self._download_btn.setEnabled(False)

        self._keys_btn = QPushButton("Ключи API…", self)
        self._keys_btn.clicked.connect(self._edit_keys)

        self._empty = EmptyState(
            "Найдите что-нибудь",
            "Bing, Openverse, LoremFlickr, Picsum и NASA работают без ключа.\n"
            "Unsplash, Pixabay и Pexels включаются по ключу.",
        )

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._grid)
        self._stack.addWidget(self._empty)

        header = QHBoxLayout()
        header.addWidget(self._provider_box)
        header.addWidget(self._query, 1)
        header.addWidget(self._search_btn)
        header.addWidget(self._daily_btn)
        header.addWidget(self._keys_btn)

        bottom = QHBoxLayout()
        bottom.addWidget(self._progress, 1)
        bottom.addWidget(self._download_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(header)
        layout.addWidget(self._stack, 1)
        layout.addLayout(bottom)

    # ------------------------------------------------------------- providers
    def _reload_providers(self) -> None:
        self._provider_box.blockSignals(True)
        self._provider_box.clear()
        for provider in sources.build_providers(self._api_keys):
            label = provider.label
            if provider.key_required and not provider.api_key:
                label += "  (нужен ключ)"
            self._provider_box.addItem(label, provider.name)
        self._provider_box.blockSignals(False)

    def _edit_keys(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Ключи API")
        dialog.setMinimumWidth(520)
        form = QVBoxLayout(dialog)
        intro = QLabel(
            "Эти сервисы бесплатны, но требуют регистрации. "
            "Ключи хранятся только у вас, в файле настроек."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Hint")
        form.addWidget(intro)

        fields = {}
        for cls in sources.PROVIDERS:
            if not cls.key_required:
                continue
            label = QLineEdit(self._api_keys.get(cls.name, ""), dialog)
            label.setPlaceholderText(cls.key_hint)
            label.setEchoMode(QLineEdit.Password)
            fields[cls.name] = label
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{cls.label}:"))
            row.addWidget(label, 1)
            form.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addWidget(buttons)

        if dialog.exec() == QDialog.Accepted:
            for name, field in fields.items():
                value = field.text().strip()
                if value:
                    self._api_keys[name] = value
                else:
                    self._api_keys.pop(name, None)
            self._config.set("source_api_keys", self._api_keys)
            self._reload_providers()
            self.status.emit("Ключи API сохранены")

    def _current_provider(self) -> Optional[sources.Provider]:
        name = self._provider_box.currentData()
        provider = sources.provider_by_name(name, self._api_keys.get(name or "", ""))
        if provider is None:
            return None
        if provider.key_required and not provider.api_key:
            QMessageBox.information(
                self, "Нужен ключ",
                f"Для {provider.label} требуется бесплатный ключ API.\n"
                "Нажмите «Ключи API…» и вставьте его.",
            )
            return None
        return provider

    # --------------------------------------------------------------- search
    def _search(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        query = self._query.text().strip()
        self.status.emit(f"Поиск: {provider.label} «{query}»…")
        task = _SearchTask(provider, query, 1, 40)
        task.signals.finished.connect(self._on_search_done)
        QThreadPool.globalInstance().start(task)
        self._thread_pool.append(task)

    def _load_daily(self) -> None:
        provider = self._current_provider()
        if provider is None:
            return
        self.status.emit(f"Загрузка «фото дня» от {provider.label}…")

        class _DailyTask(QRunnable):
            def __init__(self, p):
                super().__init__()
                self._p = p
                self.signals = _SearchSignals()
                self.setAutoDelete(True)

            def run(self):
                try:
                    self.signals.finished.emit(self._p.name, self._p.daily(), "")
                except sources.ProviderError as exc:
                    self.signals.finished.emit(self._p.name, [], str(exc))

        task = _DailyTask(provider)
        task.signals.finished.connect(self._on_search_done)
        QThreadPool.globalInstance().start(task)
        self._thread_pool.append(task)

    def _on_search_done(self, provider_name: str, results: list, error: str) -> None:
        if error:
            self.status.emit(f"Ошибка: {error}")
            QMessageBox.warning(self, "Ошибка поиска", error)
            return
        self._results = results
        self._grid.clear()
        for result in results:
            entry = QListWidgetItem()
            entry.setData(Qt.UserRole, result)
            entry.setToolTip(
                f"{result.title or 'Без названия'}\n"
                + (f"Автор: {result.author}\n" if result.author else "")
                + f"Источник: {result.provider}\n"
                + (f"Лицензия: {result.license}\n" if result.license else "")
                + (f"Размер: {result.width}×{result.height}" if result.width else "")
            )
            self._grid.addItem(entry)
        self._stack.setCurrentWidget(self._grid if results else self._empty)
        self._update_download_button()
        self.status.emit(f"Найдено результатов: {len(results)}")

    def _update_download_button(self) -> None:
        self._download_btn.setEnabled(bool(self._grid.selectedItems()) and self._active_downloads == 0)

    # ------------------------------------------------------------- downloads
    def _on_context_menu(self, pos) -> None:
        if not self._grid.itemAt(pos):
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction("Скачать выбранное", self._download_selected)
        menu.addAction("Открыть в браузере", self._open_in_browser)
        menu.exec(self._grid.viewport().mapToGlobal(pos))

    def _selected_results(self) -> list[sources.SearchResult]:
        out = []
        for item in self._grid.selectedItems():
            result = item.data(Qt.UserRole)
            if result is not None:
                out.append(result)
        return out

    def _open_in_browser(self) -> None:
        import webbrowser

        for result in self._selected_results():
            if result.source_url:
                webbrowser.open(result.source_url)
                return

    def _download_selected(self) -> None:
        chosen = self._selected_results()
        if not chosen:
            return
        folder = self._config.get("download_folder") or str(paths.wallpaper_dir())
        destination_dir = Path(folder)
        destination_dir.mkdir(parents=True, exist_ok=True)

        for result in chosen:
            filename = sources.suggest_filename(result)
            destination = destination_dir / filename
            destination = unique_path(destination)
            task = _DownloadTask(result, destination)
            task.signals.progress.connect(self._on_progress)
            task.signals.finished.connect(self._on_download_done)
            QThreadPool.globalInstance().start(task)
            self._thread_pool.append(task)
        self._active_downloads += len(chosen)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._download_btn.setEnabled(False)
        self.status.emit(f"Загрузка начата: {len(chosen)} изображений")

    def _on_progress(self, read: int, total: int) -> None:
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(read)
        else:
            self._progress.setMaximum(0)  # busy indicator

    def _on_download_done(self, result, path, error: str) -> None:
        self._active_downloads = max(0, self._active_downloads - 1)
        if error or path is None:
            self.status.emit(f"Не удалось скачать: {error or 'сетевая ошибка'}")
        else:
            wallpaper = sources.result_to_wallpaper(result, Path(path))
            self._library.add(wallpaper)
            self.status.emit(f"Скачано: {wallpaper.name}")
        if self._active_downloads == 0:
            self._progress.setVisible(False)
            self.download_finished.emit()
        self._update_download_button()


__all__ = ["SourcesView"]