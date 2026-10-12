"""Wallpaper library: discovery, metadata and import."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from . import paths, utils
from .models import (
    IMAGE_SUFFIXES,
    SCENE_SUFFIXES,
    VIDEO_SUFFIXES,
    Wallpaper,
    WallpaperKind,
    id_for_path,
)

log = logging.getLogger(__name__)

INDEX_FILENAME = "library.json"

#: Folders that are never worth indexing.
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".cache",
    "$RECYCLE.BIN", "System Volume Information", ".thumbnails",
}


class Library:
    """Keeps an in-memory index of every wallpaper Wallflow knows about.

    The index is persisted to ``library.json`` so favourites, tags and custom
    names survive restarts. Paths on disk are the source of truth for existence:
    an entry whose file vanished is dropped on the next scan.
    """

    def __init__(self, extra_dirs: Iterable[Path] | None = None) -> None:
        self._root = paths.wallpaper_dir()
        self._index_path = paths.data_dir() / INDEX_FILENAME
        self._items: dict[str, Wallpaper] = {}
        self._dirs: list[Path] = []
        self.load()
        self._dirs = self._resolve_dirs(extra_dirs)

    # ------------------------------------------------------------ locations
    @property
    def root(self) -> Path:
        return self._root

    @property
    def directories(self) -> list[Path]:
        return list(self._dirs)

    def _resolve_dirs(self, extra_dirs: Iterable[Path] | None) -> list[Path]:
        dirs: list[Path] = [self._root]
        for candidate in extra_dirs or ():
            p = Path(candidate).expanduser()
            if p.is_dir():
                dirs.append(p)
        seen: set[Path] = set()
        unique: list[Path] = []
        for d in dirs:
            resolved = d.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
        return unique

    def set_directories(self, dirs: Iterable[Path]) -> None:
        self._dirs = self._resolve_dirs(dirs)
        self.scan()

    # --------------------------------------------------------------- access
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Wallpaper]:
        return iter(self._items.values())

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._items

    def all(self) -> list[Wallpaper]:
        return list(self._items.values())

    def get(self, item_id: str) -> Optional[Wallpaper]:
        item = self._items.get(item_id)
        if item is not None and item.is_valid:
            return item
        return None

    def by_path(self, path: Path | str) -> Optional[Wallpaper]:
        return self._items.get(id_for_path(Path(path)))

    def filtered(
        self,
        *,
        search: str = "",
        kinds: Optional[set[WallpaperKind]] = None,
        favourites_only: bool = False,
        tags: Optional[set[str]] = None,
        folder: str = "",
    ) -> list[Wallpaper]:
        needle = search.strip().lower()
        result: list[Wallpaper] = []
        for item in self._items.values():
            if not item.is_valid:
                continue
            if favourites_only and not item.favourite:
                continue
            if kinds and item.wallpaper_kind not in kinds:
                continue
            if tags and not (tags & set(item.tags)):
                continue
            if folder:
                try:
                    if Path(item.path).parent != Path(folder).resolve():
                        continue
                except OSError:
                    continue
            if needle:
                haystack = " ".join(
                    [item.name, item.author, item.description, " ".join(item.tags)]
                ).lower()
                if needle not in haystack:
                    continue
            result.append(item)
        result.sort(key=lambda w: (w.favourite, w.name.lower()), reverse=True)
        return result

    def tags(self) -> list[str]:
        seen: dict[str, int] = {}
        for item in self._items.values():
            for tag in item.tags:
                seen[tag] = seen.get(tag, 0) + 1
        return sorted(seen, key=lambda t: (-seen[t], t.lower()))

    # --------------------------------------------------------------- mutate
    def add(self, wallpaper: Wallpaper, *, save: bool = True) -> Wallpaper:
        """Register a wallpaper in the index.

        Persists immediately so a crash right after an import cannot lose the
        work. Bulk callers pass ``save=False`` and call :meth:`save` once at the
        end instead of rewriting the index per file.
        """
        existing = self._items.get(wallpaper.id)
        if existing is not None:
            # Preserve user-editable fields when re-adding the same file.
            wallpaper.favourite = existing.favourite
            wallpaper.tags = existing.tags or wallpaper.tags
            wallpaper.description = existing.description or wallpaper.description
            wallpaper.added_at = existing.added_at or wallpaper.added_at
        if not wallpaper.added_at:
            wallpaper.added_at = time.time()
        self._items[wallpaper.id] = wallpaper
        if save:
            self.save()
        return wallpaper

    def remove(self, item_id: str, *, delete_file: bool = False) -> bool:
        item = self._items.pop(item_id, None)
        if item is None:
            return False
        if delete_file:
            _remove_item_files(item)
        self.save()
        return True

    def update(self, item_id: str, **changes) -> bool:
        item = self._items.get(item_id)
        if item is None:
            return False
        for key, value in changes.items():
            if hasattr(item, key):
                setattr(item, key, value)
        self.save()
        return True

    def toggle_favourite(self, item_id: str) -> bool:
        item = self._items.get(item_id)
        if item is None:
            return False
        item.favourite = not item.favourite
        self.save()
        return True

    # ---------------------------------------------------------------- scan
    def scan(self, *, progress: Optional[Callable[[int, str], None]] = None) -> int:
        """Walk the library directories and refresh the index.

        Returns the number of files added or updated.
        """
        found = 0
        seen: set[str] = set()
        for directory in self._dirs:
            for path in _walk(directory):
                found += 1
                if progress:
                    progress(found, str(path))
                item_id = id_for_path(path)
                seen.add(item_id)
                existing = self._items.get(item_id)
                if existing is not None:
                    mtime = path.stat().st_mtime
                    if existing.added_at and mtime <= existing.added_at:
                        continue
                wallpaper = Wallpaper.from_path(path)
                wallpaper.kind = _kind_for(path).value
                if existing is not None:
                    self.add(wallpaper)
                else:
                    wallpaper.added_at = path.stat().st_mtime or time.time()
                    self._items[wallpaper.id] = wallpaper

        # Drop entries whose files are gone.
        stale = [i for i in self._items if i not in seen and not self._items[i].is_valid]
        for item_id in stale:
            self._items.pop(item_id, None)

        self.save()
        log.info("Library scan finished: %d files, %d entries", found, len(self._items))
        return found

    # ----------------------------------------------------------------- I/O
    def load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Cannot read library index: %s", exc)
            return
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return
        for entry in items:
            try:
                wallpaper = Wallpaper.from_dict(entry)
            except (TypeError, ValueError):
                continue
            self._items[wallpaper.id] = wallpaper

    def save(self) -> None:
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "items": [w.to_dict() for w in self._items.values()],
        }
        tmp = self._index_path.with_suffix(".json.tmp")
        try:
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._index_path)
        except OSError as exc:
            log.error("Cannot save library index: %s", exc)

    # --------------------------------------------------------------- import
    def import_file(
        self,
        path: Path | str,
        *,
        move: bool = False,
        subfolder: str = "",
        author: str = "",
        source_url: str = "",
        save: bool = True,
    ) -> Optional[Wallpaper]:
        """Copy (or move) a file into the library and index it."""
        src = Path(path).expanduser()
        if not src.is_file():
            log.warning("Cannot import missing file: %s", src)
            return None

        kind = _kind_for(src)
        folder = self._root / utils.safe_filename(subfolder) if subfolder else self._root
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / utils.safe_filename(src.name, "wallpaper")
        target = utils.unique_path(target)

        try:
            if move:
                shutil.move(str(src), str(target))
            else:
                shutil.copy2(src, target)
        except (OSError, shutil.Error) as exc:
            log.error("Import failed for %s: %s", src, exc)
            return None

        wallpaper = Wallpaper.from_path(target)
        wallpaper.kind = kind.value
        wallpaper.author = author
        wallpaper.source_url = source_url
        if subfolder:
            wallpaper.tags = [utils.safe_filename(subfolder, "imported")]
        return self.add(wallpaper, save=save)

    def import_dir(self, path: Path | str, *, move: bool = False) -> int:
        """Import every supported file inside a folder, keeping its structure."""
        root = Path(path).expanduser()
        if not root.is_dir():
            return 0
        count = 0
        for file_path in _walk(root):
            relative = file_path.relative_to(root).parent
            sub = str(relative) if str(relative) != "." else ""
            # Only keep the first two levels to avoid pathological trees.
            parts = [p for p in Path(sub).parts if p != "."][:2] if sub else []
            subfolder = "/".join(utils.safe_filename(p, "folder") for p in parts)
            if self.import_file(file_path, move=move, subfolder=subfolder, save=False):
                count += 1
        # One write for the whole batch instead of one per file.
        self.save()
        return count


def _walk(directory: Path) -> Iterator[Path]:
    """Yield supported wallpaper files under ``directory``.

    Skip rules are applied to the path *relative to the scan root*: comparing
    absolute parts would reject everything inside e.g. ``~/.local/share``.
    """
    suffixes = IMAGE_SUFFIXES | VIDEO_SUFFIXES | SCENE_SUFFIXES | {".html", ".zip"}
    try:
        for entry in sorted(directory.rglob("*")):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            try:
                relative = entry.relative_to(directory)
            except ValueError:
                continue
            if any(part in SKIP_DIRS or part.startswith(".") for part in relative.parts[:-1]):
                continue
            if entry.suffix.lower() in suffixes:
                yield entry
            elif entry.suffix.lower() == ".html" and entry.name.lower() == "index.html":
                yield entry.parent  # a web wallpaper project
    except OSError as exc:
        log.debug("Could not walk %s: %s", directory, exc)


def _kind_for(path: Path) -> WallpaperKind:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return WallpaperKind.WEB
    if suffix in SCENE_SUFFIXES:
        return WallpaperKind.SCENE
    if suffix in VIDEO_SUFFIXES:
        return WallpaperKind.VIDEO
    return WallpaperKind.IMAGE


def _remove_item_files(wallpaper: Wallpaper) -> None:
    """Delete the wallpaper and, for web projects, its whole folder."""
    path = Path(wallpaper.path)
    try:
        if wallpaper.wallpaper_kind is WallpaperKind.WEB and path.name.lower() == "index.html":
            shutil.rmtree(path.parent, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError as exc:
        log.error("Could not delete %s: %s", path, exc)


def checksum(path: Path | str, *, length: int = 4096) -> str:
    """Quick content hash used to detect duplicated downloads."""
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            digest.update(fh.read(length))
    except OSError:
        return ""
    return digest.hexdigest()[:16]


__all__ = ["Library", "checksum", "SKIP_DIRS", "INDEX_FILENAME"]