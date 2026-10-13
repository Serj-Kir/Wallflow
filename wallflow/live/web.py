"""HTML/CSS/JS live wallpapers (Wallpaper Engine's "Web" type).

A web wallpaper is a folder containing an ``index.html`` plus whatever assets
and scripts it needs. An optional ``project.json`` provides metadata:

.. code-block:: json

    {
      "title": "Aurora",
      "author": "someone",
      "description": "Northern lights",
      "preview": "preview.jpg",
      "background": "#04070f"
    }

Qt Web Engine is an optional dependency: if it is missing the app still runs and
simply reports that web wallpapers are unavailable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from .renderer import WallpaperWidget

log = logging.getLogger(__name__)

WEBENGINE_AVAILABLE = True
WEBENGINE_IMPORT_ERROR = ""

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
except Exception as _exc:  # pragma: no cover - optional dependency
    WEBENGINE_AVAILABLE = False
    WEBENGINE_IMPORT_ERROR = str(_exc)
    QWebEnginePage = QWebEngineView = None  # type: ignore[assignment]


@dataclass
class WebProject:
    """Metadata for a web wallpaper folder."""

    root: Path
    title: str = ""
    author: str = ""
    description: str = ""
    preview: Optional[Path] = None
    background: str = "#000000"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def entry(self) -> Optional[Path]:
        index = self.root / "index.html"
        return index if index.exists() else None

    @property
    def is_valid(self) -> bool:
        return self.entry is not None

    @property
    def name(self) -> str:
        return self.title or self.root.name

    @classmethod
    def load(cls, path: Path | str) -> "WebProject":
        root = Path(path).expanduser()
        if root.is_file() and root.name.lower() == "index.html":
            root = root.parent
        project = cls(root=root, title=root.name)
        meta = root / "project.json"
        if not meta.exists():
            # Also accept the lowercase variant used by some tools.
            meta = root / "Project.json"
        if meta.exists():
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Bad project.json in %s: %s", root, exc)
                data = {}
            if isinstance(data, dict):
                project.raw = data
                project.title = str(data.get("title") or data.get("name") or root.name)
                project.author = str(data.get("author") or data.get("creator") or "")
                project.description = str(data.get("description") or "")
                preview = data.get("preview") or data.get("thumbnail")
                if isinstance(preview, str) and preview:
                    candidate = root / preview
                    project.preview = candidate if candidate.exists() else None
                background = data.get("background") or data.get("backgroundColor")
                if isinstance(background, str) and background:
                    project.background = background
        return project

    def describe(self) -> str:
        parts = [self.name]
        if self.author:
            parts.append(f"by {self.author}")
        if self.description:
            parts.append(self.description)
        return " · ".join(parts)


class _Bridge(QObject):
    """Exposes a small ``window.wallflow`` API to the page.

    Pages can use it for user interaction without ever touching the filesystem.
    """

    clicked = Signal()

    def pause(self) -> None:
        self.clicked.emit()


class WebWidget(WallpaperWidget):
    """Renders an HTML wallpaper in an offscreen-registered Chromium view."""

    error = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        if not WEBENGINE_AVAILABLE:
            super().__init__(parent)
            self._view = None
            self._project = None
            self.error.emit(
                "Qt Web Engine недоступен: " + (WEBENGINE_IMPORT_ERROR or "не установлен")
            )
            return

        super().__init__(parent)
        self._view = QWebEngineView(self)
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        self._view.setFocusPolicy(Qt.NoFocus)
        self._bridge = _Bridge(self)
        self._bridge.clicked.connect(self._toggle_pause)
        self._project: Optional[WebProject] = None
        self._last_error = ""

        settings = self._view.settings()
        # Attribute names differ slightly between Qt 6 minor versions, so each
        # one is applied defensively rather than assumed to exist.
        for attribute, value in (
            ("LocalContentCanAccessFileUrls", True),
            ("LocalContentCanAccessRemoteUrls", True),
            ("ShowScrollBars", False),
            ("FullScreenSupportEnabled", False),
            ("DnsPrefetchEnabled", False),
            ("HyperlinkAuditingEnabled", False),
            ("AutoLoadImages", True),
            ("JavascriptCanOpenWindows", False),
            ("ScreenCaptureEnabled", False),
            ("WebRTCPublicInterfacesOnly", True),
        ):
            enum_value = getattr(QWebEngineSettings.WebAttribute, attribute, None)
            if enum_value is not None:
                settings.setAttribute(enum_value, value)

        self._view.page().setBackgroundColor(QColor("#000000"))
        self._resize_child()

    # ------------------------------------------------------------------ api
    def set_source(self, path: Path | str) -> None:
        if not WEBENGINE_AVAILABLE or self._view is None:
            return
        project = WebProject.load(path)
        self._project = project
        entry = project.entry
        if entry is None:
            self.error.emit(f"В папке нет index.html: {project.root.name}")
            return
        self._source = project.root
        self._view.page().setBackgroundColor(QColor(project.background))
        self._view.load(QUrl.fromLocalFile(str(entry.resolve())))
        self._view.page().loadFinished.connect(self._on_load_finished)
        self.start()

    @property
    def project(self) -> Optional[WebProject]:
        return self._project

    def start(self) -> None:
        super().start()
        if self._view is None:
            return
        self._view.setVisible(True)
        # Let the page resume after a pause.
        self._view.page().runJavaScript(
            "window.__wfResume && window.__wfResume.forEach(function(fn){ try{fn();}catch(e){} });"
        )

    def pause(self) -> None:
        super().pause()
        if self._view is None:
            return
        self._view.page().runJavaScript(
            "window.__wfPause && window.__wfPause.forEach(function(fn){ try{fn();}catch(e){} });"
        )
        # Hiding the view makes Chromium stop producing frames, which is the
        # cheapest way to halt an animated page.
        self._view.setVisible(False)

    def stop(self) -> None:
        self.pause()
        if self._view is not None:
            self._view.setUrl(QUrl("about:blank"))

    def _toggle_pause(self) -> None:
        if self._running:
            self.pause()
        else:
            self.start()

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            self._last_error = "Не удалось загрузить страницу"
            self.error.emit(self._last_error)
            return
        # Let the page know Wallflow is driving it, so authors can stop expensive
        # work when the wallpaper is not the active profile.
        self._view.page().runJavaScript(
            "window.wallflow = window.wallflow || {};"
            "Object.defineProperty(window.wallflow,'version',{get:function(){return '1.0';}});"
            "window.wallflow.onPause=function(fn){"
            "  (window.__wfPause=window.__wfPause||[]).push(fn);};"
            "window.wallflow.onResume=function(fn){"
            "  (window.__wfResume=window.__wfResume||[]).push(fn);};"
        )

    def _resize_child(self) -> None:
        if self._view is not None:
            self._view.setGeometry(0, 0, self.width(), self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._resize_child()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._resize_child()

    def paintEvent(self, event) -> None:  # noqa: N802
        # The child view paints itself; this only covers the moment before the
        # first frame arrives so the wallpaper is never a harsh flash of white.
        if self._view is None or self._view.isVisible():
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))


def find_web_root(path: Path | str) -> Optional[Path]:
    """Given a file inside a web project, return the folder root."""
    p = Path(path).expanduser()
    if p.is_dir() and (p / "index.html").exists():
        return p
    if p.name.lower() == "index.html":
        return p.parent
    return None


__all__ = ["WebWidget", "WebProject", "WEBENGINE_AVAILABLE", "WEBENGINE_IMPORT_ERROR", "find_web_root"]