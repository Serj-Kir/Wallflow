"""Core data model for Wallflow.

Everything that gets persisted is defined here as plain dataclasses so it can
be serialised to JSON without pulling in an ORM or schema library.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Iterable


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def id_for_path(path: Path | str) -> str:
    """Stable id derived from the absolute path.

    Using a hash keeps ids identical across machines that share a library
    folder, and avoids leaking the full path into profile files.
    """
    raw = str(Path(path).expanduser().resolve()).lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class WallpaperKind(str, Enum):
    """The rendering pipeline a wallpaper needs."""

    IMAGE = "image"
    VIDEO = "video"
    WEB = "web"      # HTML/CSS/JS
    SCENE = "scene"  # declarative particle/animation scene

    @classmethod
    def from_path(cls, path: Path) -> "WallpaperKind":
        return kind_for_suffix(path.suffix.lower())

    @property
    def is_animated(self) -> bool:
        return self in (WallpaperKind.VIDEO, WallpaperKind.WEB, WallpaperKind.SCENE)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff", ".avif"}
VIDEO_SUFFIXES = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg"}
SCENE_SUFFIXES = {".scene"}


def kind_for_suffix(suffix: str) -> WallpaperKind:
    if suffix in IMAGE_SUFFIXES:
        return WallpaperKind.IMAGE
    if suffix in VIDEO_SUFFIXES:
        return WallpaperKind.VIDEO
    if suffix in SCENE_SUFFIXES:
        return WallpaperKind.SCENE
    # Unknown extensions are treated as web projects when they contain an
    # index.html next to them; otherwise fall back to a static image.
    return WallpaperKind.IMAGE


def _kind_for_path(path: Path) -> WallpaperKind:
    """A folder holding an index.html is a web wallpaper, not an image."""
    if path.is_dir() or path.name.lower() == "index.html":
        return WallpaperKind.WEB
    return kind_for_suffix(path.suffix.lower())


class TransitionKind(str, Enum):
    NONE = "none"
    FADE = "fade"
    SLIDE = "slide"
    ZOOM = "zoom"
    WIPE = "wipe"


class PauseReason(str, Enum):
    USER = "user"
    FULLSCREEN = "fullscreen"
    BATTERY = "battery"
    LOCKED = "locked"
    REMOTE = "remote_session"
    SUSPEND = "suspend"
    PLAYBACK = "playback"


@dataclass
class Wallpaper:
    """A single wallpaper entry in the library."""

    id: str
    path: str
    kind: str = WallpaperKind.IMAGE.value
    name: str = ""
    author: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    source_url: str = ""
    added_at: float = 0.0
    favourite: bool = False

    @classmethod
    def from_path(cls, path: Path | str, **kwargs: Any) -> "Wallpaper":
        p = Path(path).expanduser().resolve()
        # Detect the kind from the file itself so callers do not have to repeat
        # it; an explicit ``kind`` in kwargs still wins.
        kwargs.setdefault("kind", _kind_for_path(p).value)
        return cls(id=id_for_path(p), path=str(p), name=p.stem, **kwargs)

    @property
    def file_path(self) -> Path:
        return Path(self.path)

    @property
    def wallpaper_kind(self) -> WallpaperKind:
        try:
            return WallpaperKind(self.kind)
        except ValueError:
            return WallpaperKind.IMAGE

    @property
    def is_animated(self) -> bool:
        return self.wallpaper_kind.is_animated

    @property
    def is_valid(self) -> bool:
        return Path(self.path).exists()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Wallpaper":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class MonitorAssignment:
    """Maps a physical monitor to a wallpaper.

    ``wallpaper_id`` of ``None`` means "follow the profile's main wallpaper".
    """

    monitor_id: str
    wallpaper_id: str | None = None
    fit: str = "fill"
    alignment: str = "center"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorAssignment":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class Profile:
    """A named wallpaper setup that can be switched or scheduled."""

    id: str = field(default_factory=new_id)
    name: str = "Profile"
    wallpaper_id: str | None = None
    playlist_id: str | None = None
    monitors: list[MonitorAssignment] = field(default_factory=list)
    accent_color: str = ""  # "#rrggbb", empty = auto-detect
    locked: bool = False

    def monitor_map(self) -> dict[str, str | None]:
        return {m.monitor_id: m.wallpaper_id for m in self.monitors}

    def set_monitor(self, monitor_id: str, wallpaper_id: str | None) -> None:
        for m in self.monitors:
            if m.monitor_id == monitor_id:
                m.wallpaper_id = wallpaper_id
                return
        self.monitors.append(MonitorAssignment(monitor_id, wallpaper_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "wallpaper_id": self.wallpaper_id,
            "playlist_id": self.playlist_id,
            "monitors": [m.to_dict() for m in self.monitors],
            "accent_color": self.accent_color,
            "locked": self.locked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        return cls(
            id=data.get("id") or new_id(),
            name=data.get("name", "Profile"),
            wallpaper_id=data.get("wallpaper_id"),
            playlist_id=data.get("playlist_id"),
            monitors=[MonitorAssignment.from_dict(m) for m in data.get("monitors", [])],
            accent_color=data.get("accent_color", ""),
            locked=bool(data.get("locked", False)),
        )


@dataclass
class Playlist:
    """An ordered or shuffled sequence of wallpapers."""

    id: str = field(default_factory=new_id)
    name: str = "Playlist"
    items: list[str] = field(default_factory=list)  # wallpaper ids
    shuffle: bool = False
    #: Seconds between changes; 0 means "only on manual next / rules".
    interval_seconds: int = 0
    transition: str = TransitionKind.FADE.value
    transition_ms: int = 800
    paused: bool = False

    def __len__(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Playlist":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ScheduleRule:
    """A time-based rule that activates a profile.

    This is Wallflow's cross-platform stand-in for Wallpaper Engine's
    application rules, which only work on Windows.
    """

    id: str = field(default_factory=new_id)
    name: str = ""
    enabled: bool = True
    #: 0 = Monday .. 6 = Sunday; empty means every day.
    weekdays: list[int] = field(default_factory=list)
    start_minutes: int = 0  # minutes since midnight
    end_minutes: int = 24 * 60
    profile_id: str = ""
    priority: int = 0

    def matches(self, minutes_of_day: int, weekday: int) -> bool:
        if not self.enabled:
            return False
        if self.weekdays and weekday not in self.weekdays:
            return False
        start, end = self.start_minutes, self.end_minutes
        if start == end:
            return True
        if start < end:
            return start <= minutes_of_day < end
        # Rule wraps past midnight.
        return minutes_of_day >= start or minutes_of_day < end

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduleRule":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class MonitorInfo:
    """Description of a connected display."""

    id: str
    name: str = ""
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0
    scale: float = 1.0
    primary: bool = False
    #: Backing Qt/DBus identifies the display with.
    identifier: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorInfo":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def dedupe(items: Iterable[str]) -> list[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


__all__ = [
    "new_id",
    "id_for_path",
    "Wallpaper",
    "WallpaperKind",
    "TransitionKind",
    "PauseReason",
    "MonitorAssignment",
    "Profile",
    "Playlist",
    "ScheduleRule",
    "MonitorInfo",
    "kind_for_suffix",
    "IMAGE_SUFFIXES",
    "VIDEO_SUFFIXES",
    "SCENE_SUFFIXES",
    "dedupe",
]