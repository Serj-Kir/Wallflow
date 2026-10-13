"""Live wallpaper renderers and the factory that picks one for a file."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..models import WallpaperKind, kind_for_suffix
from .renderer import ImageWidget, SceneWidget, WallpaperWidget

log = logging.getLogger(__name__)

# Qt Multimedia powers video playback, Qt Web Engine powers HTML wallpapers.
# Both ship in the standard PySide6 wheel, but the app must still start if a
# minimal install is missing them.
try:
    from .video import LOOPS_FOREVER, LOOPS_ONCE, VideoWidget

    VIDEO_AVAILABLE = True
    VIDEO_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - depends on the install
    VideoWidget = None  # type: ignore[assignment]
    LOOPS_FOREVER, LOOPS_ONCE = -1, 1
    VIDEO_AVAILABLE = False
    VIDEO_IMPORT_ERROR = str(_exc)

try:
    from .web import WEBENGINE_AVAILABLE, WEBENGINE_IMPORT_ERROR, WebWidget
except Exception as _exc:  # pragma: no cover
    WebWidget = None  # type: ignore[assignment]
    WEBENGINE_AVAILABLE = False
    WEBENGINE_IMPORT_ERROR = str(_exc)

__all__ = [
    "ImageWidget",
    "SceneWidget",
    "WallpaperWidget",
    "VideoWidget",
    "WebWidget",
    "VIDEO_AVAILABLE",
    "WEBENGINE_AVAILABLE",
    "LOOPS_FOREVER",
    "LOOPS_ONCE",
    "widget_for_kind",
    "create_widget",
    "capabilities",
]


def widget_for_kind(
    kind: WallpaperKind, *, muted: bool = True, loops: int = LOOPS_FOREVER, parent=None
) -> Optional[WallpaperWidget]:
    """Instantiate the widget that can render ``kind``, or None if unsupported."""
    if kind is WallpaperKind.SCENE:
        return SceneWidget(parent)
    if kind is WallpaperKind.IMAGE:
        return ImageWidget(parent)
    if kind is WallpaperKind.VIDEO:
        if VideoWidget is None:
            log.warning("Video wallpaper requested but Qt Multimedia is missing: %s", VIDEO_IMPORT_ERROR)
            return None
        return VideoWidget(muted=muted, loops=loops, parent=parent)
    if kind is WallpaperKind.WEB:
        if WebWidget is None:
            log.warning("Web wallpaper requested but Qt Web Engine is missing: %s", WEBENGINE_IMPORT_ERROR)
            return None
        return WebWidget(parent)
    return None


def create_widget(
    path: Path | str, *, muted: bool = True, loops: int = LOOPS_FOREVER, parent=None
) -> Optional[WallpaperWidget]:
    """Instantiate the right widget for a wallpaper on disk."""
    p = Path(path)
    if p.is_dir():
        return widget_for_kind(WallpaperKind.WEB, muted=muted, loops=loops, parent=parent)
    if p.suffix.lower() == ".scene":
        return widget_for_kind(WallpaperKind.SCENE, muted=muted, loops=loops, parent=parent)
    return widget_for_kind(
        kind_for_suffix(p.suffix.lower()), muted=muted, loops=loops, parent=parent
    )


def capabilities() -> dict[str, bool]:
    """Which live-wallpaper formats this installation can actually play."""
    return {
        "image": True,
        "scene": True,
        "video": VIDEO_AVAILABLE,
        "web": WEBENGINE_AVAILABLE,
    }


def unavailable_reason(kind: WallpaperKind) -> str:
    if kind is WallpaperKind.VIDEO:
        return VIDEO_IMPORT_ERROR or "Qt Multimedia не установлен"
    if kind is WallpaperKind.WEB:
        return WEBENGINE_IMPORT_ERROR or "Qt Web Engine не установлен"
    return ""