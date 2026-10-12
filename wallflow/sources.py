"""Free wallpaper providers.

Everything here works without an API key out of the box. Sources that do need
a key (Unsplash, Pixabay, Pexels) are included but only enabled once the user
stores a key in Settings → Sources.

All HTTP goes through the standard library so Wallflow stays dependency-light
and installs quickly.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import utils
from .models import Wallpaper, WallpaperKind

log = logging.getLogger(__name__)

USER_AGENT = "Wallflow/1.0 (+https://github.com/wallflow/wallflow)"
DEFAULT_TIMEOUT = 25.0

#: Keys are read from the environment first, then from the config file.
ENV_KEYS = {
    "unsplash": "WALLFLOW_UNSPLASH_KEY",
    "pixabay": "WALLFLOW_PIXABAY_KEY",
    "pexels": "WALLFLOW_PEXELS_KEY",
}


@dataclass
class SearchResult:
    """One image offered by a provider."""

    id: str
    url: str            # direct image URL for download
    thumb_url: str = ""
    title: str = ""
    author: str = ""
    source_url: str = ""
    width: int = 0
    height: int = 0
    provider: str = ""
    license: str = ""

    @property
    def aspect(self) -> float:
        if self.height and self.width:
            return self.width / self.height
        return 16.0


class ProviderError(RuntimeError):
    """Raised for user-facing provider failures (bad key, network, quota)."""


def _get(url: str, *, headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code} от {urllib.parse.urlsplit(url).netloc}") from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"Сеть недоступна: {exc.reason}") from exc
    except (TimeoutError, ssl.SSLError, OSError) as exc:
        raise ProviderError(str(exc)) from exc


def _get_json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    payload = _get(url, headers=headers)
    try:
        return json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ProviderError("Сервер вернул некорректный JSON") from exc


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
class Provider:
    """Base class for a wallpaper source."""

    name = "base"
    label = "Base"
    homepage = ""
    key_required = False
    key_hint = ""
    env_var = ""
    #: What the provider is good for, shown in the UI.
    description = ""

    def __init__(self, api_key: str = "") -> None:
        self.api_key = (api_key or os.environ.get(self.env_var, "")).strip()

    @property
    def enabled(self) -> bool:
        return not self.key_required or bool(self.api_key)

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        raise NotImplementedError

    def daily(self) -> list[SearchResult]:
        return []


class BingProvider(Provider):
    """Bing's "picture of the day". No key, one fresh image per day, several resolutions."""

    name = "bing"
    label = "Bing"
    homepage = "https://www.bing.com"
    description = "Одно качественное фото в день в нескольких разрешениях"

    ARCHIVE = "https://www.bing.com/HPImageArchive.aspx"

    def daily(self) -> list[SearchResult]:
        market = os.environ.get("WALLFLOW_BING_MARKET", "ru-RU")
        params = urllib.parse.urlencode({"format": "js", "idx": "0", "n": "16", "mkt": market})
        data = _get_json(f"{self.ARCHIVE}?{params}")
        images = data.get("images") or []
        results: list[SearchResult] = []
        for entry in images:
            url_base = entry.get("urlbase") or entry.get("url", "")
            if not url_base:
                continue
            copyright_text = entry.get("copyright") or data.get("copyright", "")
            author, title = _split_bing_copyright(copyright_text)
            results.append(
                SearchResult(
                    id=str(entry.get("hsh", url_base)),
                    url=f"{url_base}_1920x1080.jpg",
                    thumb_url=f"{url_base}_400x240.jpg",
                    title=title,
                    author=author,
                    source_url=entry.get("clickurl", "https://www.bing.com"),
                    width=1920,
                    height=1080,
                    provider=self.name,
                    license="Bing daily wallpaper",
                )
            )
        return results

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        # Bing's image search endpoint needs a subscription key; the daily
        # feed is the part that is genuinely free.
        return self.daily()[:per_page] if query.lower() in ("", "*") else []


class PicsumProvider(Provider):
    """Lorem Picsum — free placeholder photography from Unsplash, no key."""

    name = "picsum"
    label = "Lorem Picsum"
    homepage = "https://picsum.photos"
    description = "Случайные качественные фотографии, без ключа"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        limit = min(per_page, 60)
        url = f"https://picsum.photos/v2/list?page={max(1, page)}&limit={limit}"
        if query.strip():
            # The list endpoint has no search; fall back to a seeded image.
            seed = urllib.parse.quote(query.strip())
            return [
                SearchResult(
                    id=seed,
                    url=f"https://picsum.photos/seed/{seed}/1920/1080",
                    thumb_url=f"https://picsum.photos/seed/{seed}/400/225",
                    title=f"Random: {query}",
                    author="Lorem Picsum",
                    source_url="https://picsum.photos",
                    width=1920,
                    height=1080,
                    provider=self.name,
                )
            ]
        try:
            entries = _get_json(url)
        except ProviderError:
            return []
        return [
            SearchResult(
                id=str(entry.get("id")),
                url=f"https://picsum.photos/id/{entry.get('id')}/1920/1080",
                thumb_url=f"https://picsum.photos/id/{entry.get('id')}/360/202",
                title=entry.get("author", "") or "Picsum",
                author=entry.get("author", ""),
                source_url=f"https://picsum.photos/photos/{entry.get('id')}",
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                provider=self.name,
                license="Unsplash / Picsum",
            )
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        ]


class LoremflickrProvider(Provider):
    """Keyword-based free photos, no key."""

    name = "loremflickr"
    label = "LoremFlickr"
    homepage = "https://loremflickr.com"
    description = "Поиск по ключевым словам, без ключа API"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        if not query.strip():
            return []
        tags = "/".join(re.findall(r"[\w-]+", query.lower())[:3]) or "wallpaper"
        lock = f"?lock={page}" if page > 1 else ""
        return [
            SearchResult(
                id=f"{tags}-{page}-{i}",
                url=f"https://loremflickr.com/1920/1080/{tags}/all{lock}?random={i}",
                thumb_url=f"https://loremflickr.com/360/202/{tags}/all{lock}?random={i}",
                title=query,
                author="LoremFlickr",
                source_url="https://loremflickr.com",
                width=1920,
                height=1080,
                provider=self.name,
                license="CC / Flickr",
            )
            for i in range(min(per_page, 30))
        ]


class OpenverseProvider(Provider):
    """Openverse (WordPress) aggregates Creative Commons media. Search needs no key."""

    name = "openverse"
    label = "Openverse"
    homepage = "https://openverse.org"
    description = "Поиск по свободным лицензиям (CC)"

    API = "https://api.openverse.org/v1/images/"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        if not query.strip():
            return []
        params = urllib.parse.urlencode(
            {
                "q": query.strip(),
                "page": max(1, page),
                "page_size": min(per_page, 50),
                "license_type": "all-cc",
                "mature": "false",
            }
        )
        headers = {"Accept": "application/json"}
        data = _get_json(f"{self.API}?{params}", headers=headers)
        return [
            SearchResult(
                id=str(entry.get("id")),
                url=entry.get("url", ""),
                thumb_url=entry.get("thumbnail", entry.get("url", "")),
                title=entry.get("title") or "",
                author=entry.get("creator") or "",
                source_url=entry.get("foreign_landing_url", ""),
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                provider=self.name,
                license=(entry.get("license") or "").upper(),
            )
            for entry in (data.get("results") or [])
            if isinstance(entry, dict) and entry.get("url")
        ]


class NasaProvider(Provider):
    """NASA's Astronomy Picture of the Day. ``DEMO_KEY`` covers casual use."""

    name = "nasa"
    label = "NASA APOD"
    homepage = "https://apod.nasa.gov"
    description = "Астрономическое фото дня"
    env_var = ""

    def daily(self) -> list[SearchResult]:
        key = self.api_key or "DEMO_KEY"
        data = _get_json(f"https://api.nasa.gov/planetary/apod?api_key={key}")
        if not isinstance(data, dict) or "url" not in data:
            return []
        title = data.get("title") or "NASA APOD"
        return [
            SearchResult(
                id=str(data.get("date", date.today().isoformat())),
                url=data["url"],
                thumb_url=data.get("thumbnail_url", data["url"]),
                title=title,
                author="NASA",
                source_url=data.get("url", "https://apod.nasa.gov"),
                width=int(data.get("width") or 1920),
                height=int(data.get("height") or 1080),
                provider=self.name,
                license="Public domain",
            )
        ]

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        return self.daily()


class UnsplashProvider(Provider):
    """Unsplash official API. Needs a free access key."""

    name = "unsplash"
    label = "Unsplash"
    homepage = "https://unsplash.com"
    key_required = True
    key_hint = "Access Key из unsplash.com/developers"
    env_var = ENV_KEYS["unsplash"]
    description = "Огромная библиотека фото (нужен ключ)"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        if not self.api_key:
            raise ProviderError("Unsplash требует ключ доступа")
        params = urllib.parse.urlencode(
            {"query": query or "wallpaper", "per_page": min(per_page, 30), "page": max(1, page)}
        )
        data = _get_json(
            f"https://api.unsplash.com/search/photos?{params}",
            headers={"Authorization": f"Client-ID {self.api_key}", "Accept-Version": "v1"},
        )
        results = []
        for entry in data.get("results", []):
            urls = entry.get("urls", {})
            user = entry.get("user", {})
            results.append(
                SearchResult(
                    id=entry.get("id", ""),
                    url=urls.get("raw") or urls.get("regular", ""),
                    thumb_url=urls.get("small", ""),
                    title=(entry.get("description") or entry.get("alt_description") or "")[:120],
                    author=user.get("name", ""),
                    source_url=(entry.get("links", {}).get("html", "")),
                    width=int(entry.get("width") or 0),
                    height=int(entry.get("height") or 0),
                    provider=self.name,
                    license="Unsplash License",
                )
            )
        return results

    def daily(self) -> list[SearchResult]:
        if not self.api_key:
            return []
        try:
            data = _get_json(
                "https://api.unsplash.com/photos/random?orientation=landscape",
                headers={"Authorization": f"Client-ID {self.api_key}", "Accept-Version": "v1"},
            )
        except ProviderError:
            return []
        data = data if isinstance(data, list) else [data]
        results = []
        for entry in data:
            urls = entry.get("urls", {})
            user = entry.get("user", {})
            results.append(
                SearchResult(
                    id=entry.get("id", ""),
                    url=urls.get("raw", ""),
                    thumb_url=urls.get("small", ""),
                    title="Unsplash random",
                    author=user.get("name", ""),
                    source_url=entry.get("links", {}).get("html", ""),
                    width=int(entry.get("width") or 0),
                    height=int(entry.get("height") or 0),
                    provider=self.name,
                    license="Unsplash License",
                )
            )
        return results


class PixabayProvider(Provider):
    name = "pixabay"
    label = "Pixabay"
    homepage = "https://pixabay.com"
    key_required = True
    key_hint = "API Key из pixabay.com/api/docs"
    env_var = ENV_KEYS["pixabay"]
    description = "Фото и векторные обои (нужен ключ)"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        if not self.api_key:
            raise ProviderError("Pixabay требует ключ API")
        params = urllib.parse.urlencode(
            {
                "key": self.api_key,
                "q": query or "wallpaper",
                "image_type": "photo",
                "orientation": "horizontal",
                "min_width": 1920,
                "per_page": min(per_page, 200),
                "page": max(1, page),
            }
        )
        data = _get_json(f"https://pixabay.com/api/?{params}")
        return [
            SearchResult(
                id=str(entry.get("id")),
                url=entry.get("largeImageURL", entry.get("webformatURL", "")),
                thumb_url=entry.get("previewURL", ""),
                title=(entry.get("tags") or "").split(",")[0] or "Pixabay",
                author=entry.get("user", ""),
                source_url=entry.get("pageURL", ""),
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                provider=self.name,
                license="Pixabay License",
            )
            for entry in (data.get("hits") or [])
            if entry.get("largeImageURL")
        ]


class PexelsProvider(Provider):
    name = "pexels"
    label = "Pexels"
    homepage = "https://pexels.com"
    key_required = True
    key_hint = "API Key из pexels.com/api"
    env_var = ENV_KEYS["pexels"]
    description = "Бесплатные фото высокого разрешения (нужен ключ)"

    def search(self, query: str, page: int = 1, per_page: int = 30) -> list[SearchResult]:
        if not self.api_key:
            raise ProviderError("Pexels требует ключ API")
        params = urllib.parse.urlencode(
            {"query": query or "wallpaper", "per_page": min(per_page, 80), "page": max(1, page)}
        )
        data = _get_json(
            f"https://api.pexels.com/v1/search?{params}",
            headers={"Authorization": self.api_key},
        )
        return [
            SearchResult(
                id=str(entry.get("id")),
                url=(entry.get("src", {}) or {}).get("original", ""),
                thumb_url=(entry.get("src", {}) or {}).get("medium", ""),
                title=entry.get("alt", "") or "Pexels",
                author=entry.get("photographer", ""),
                source_url=entry.get("url", ""),
                width=int(entry.get("width") or 0),
                height=int(entry.get("height") or 0),
                provider=self.name,
                license="Pexels License",
            )
            for entry in (data.get("photos") or [])
            if entry.get("src", {}).get("original")
        ]


PROVIDERS: tuple[type[Provider], ...] = (
    BingProvider,
    OpenverseProvider,
    LoremflickrProvider,
    PicsumProvider,
    NasaProvider,
    UnsplashProvider,
    PixabayProvider,
    PexelsProvider,
)


def build_providers(api_keys: Optional[dict[str, str]] = None) -> list[Provider]:
    keys = api_keys or {}
    return [cls(keys.get(cls.name, "")) for cls in PROVIDERS]


def provider_by_name(name: str, api_key: str = "") -> Optional[Provider]:
    for cls in PROVIDERS:
        if cls.name == name:
            return cls(api_key)
    return None


def _split_bing_copyright(text: str) -> tuple[str, str]:
    """Bing returns 'Author (Title)'; split that into a tidy author/title pair."""
    match = re.match(r"\s*(.*?)\s*\((.*)\)\s*$", text or "")
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return (text or "Bing").strip(), ""


def download(result: SearchResult, destination: Path, *, progress: Optional[Callable[[int, int], None]] = None) -> Optional[Path]:
    """Download a search result to ``destination``.

    ``destination`` is the final path; a temporary file is written first so an
    interrupted download never leaves a truncated image in the library.
    """
    if not result.url:
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")

    try:
        request = urllib.request.Request(result.url, headers={"User-Agent": USER_AGENT})
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT, context=context) as response:
            total = int(response.headers.get("Content-Length") or 0)
            read = 0
            with open(tmp, "wb") as fh:
                while True:
                    chunk = response.read(64 * 512)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read += len(chunk)
                    if progress and total:
                        progress(read, total)
        if read == 0:
            raise ProviderError("Пустой ответ сервера")
    except (urllib.error.URLError, OSError, ProviderError) as exc:
        log.warning("Download failed (%s): %s", result.url, exc)
        tmp.unlink(missing_ok=True)
        return None

    tmp.replace(destination)
    return destination


def guess_extension(url: str, fallback: str = ".jpg") -> str:
    """Pick a sensible file extension from a URL's content type or path."""
    path = urllib.parse.urlsplit(url).path
    suffix = Path(urllib.parse.unquote(path)).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    guessed = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or "")
    return guessed or fallback


def suggest_filename(result: SearchResult) -> str:
    title = utils.safe_filename(result.title or result.provider or "wallpaper", "wallpaper")
    return f"{result.provider}-{title}{guess_extension(result.url)}"


def result_to_wallpaper(result: SearchResult, local_path: Path) -> Wallpaper:
    wallpaper = Wallpaper.from_path(local_path)
    wallpaper.kind = WallpaperKind.IMAGE.value
    wallpaper.name = result.title or wallpaper.name
    wallpaper.author = result.author
    wallpaper.source_url = result.source_url
    wallpaper.tags = [result.provider, result.license] if result.license else [result.provider]
    return wallpaper


__all__ = [
    "Provider",
    "SearchResult",
    "ProviderError",
    "PROVIDERS",
    "build_providers",
    "provider_by_name",
    "download",
    "guess_extension",
    "suggest_filename",
    "result_to_wallpaper",
]