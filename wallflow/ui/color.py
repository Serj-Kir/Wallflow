"""Dominant colour extraction, used to tint the shell to match the wallpaper."""

from __future__ import annotations

import colorsys
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    HAVE_QT = True
except Exception:  # pragma: no cover
    HAVE_QT = False


def dominant_color(path: Path | str, *, fallback: str = "#20202a", skip_alpha: bool = True) -> str:
    """Return a readable accent colour for ``path`` as ``#rrggbb``.

    The image is scaled down before sampling: a 4K wallpaper would otherwise
    take hundreds of milliseconds to decode. Results are pushed towards
    saturation and brightness so the colour reads well behind text.
    """
    p = Path(path)
    if not p.exists():
        return fallback
    try:
        if HAVE_QT:
            image = QImage(str(p))
            if not image.isNull():
                return dominant_from_image(image, fallback=fallback, skip_alpha=skip_alpha)
    except Exception:  # noqa: BLE001
        log.debug("Qt colour extraction failed for %s", p, exc_info=True)

    try:
        from PIL import Image  # optional fallback
    except ImportError:
        return fallback
    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((96, 96))
            pixels = list(im.getdata())
    except Exception:  # noqa: BLE001
        log.debug("Pillow colour extraction failed for %s", p, exc_info=True)
        return fallback
    if not pixels:
        return fallback
    return _pick_accent(pixels, fallback=fallback)


def dominant_from_image(image: "QImage", *, fallback: str = "#20202a", skip_alpha: bool = True) -> str:
    scaled = image.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    width = scaled.width()
    height = scaled.height()
    if width <= 0 or height <= 0:
        return fallback
    pixels = []
    step_x = max(1, width // 48)
    step_y = max(1, height // 48)
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            pixel = scaled.pixelColor(x, y)
            if skip_alpha and pixel.alpha() < 32:
                continue
            pixels.append((pixel.red(), pixel.green(), pixel.blue()))
    if not pixels:
        return fallback
    return _pick_accent(pixels, fallback=fallback)


def _pick_accent(pixels: list[tuple[int, int, int]], *, fallback: str) -> str:
    """Choose the most visually prominent colour and make it usable."""
    buckets: dict[tuple[int, int, int], float] = {}
    for r, g, b in pixels:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        # Ignore near-black and near-white: they make poor accents.
        if v < 0.14 or v > 0.96 or s < 0.12:
            continue
        key = (int(h * 24) % 24, int(s * 6) % 6, int(v * 6) % 6)
        # Weight by saturation and brightness so vivid colours win.
        buckets[key] = buckets.get(key, 0.0) + (0.35 + s) * (0.35 + v)

    if not buckets:
        return fallback

    (h_bin, s_bin, v_bin), _score = max(buckets.items(), key=lambda kv: kv[1])
    hue = (h_bin + 0.5) / 24.0
    saturation = min(1.0, 0.42 + (s_bin + 0.5) / 6.0 * 0.55)
    value = min(1.0, 0.55 + (v_bin + 0.5) / 6.0 * 0.45)
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def contrasting_text(hex_color: str) -> str:
    """Return black or white, whichever reads better on ``hex_color``."""
    text = hex_color.lstrip("#")
    if len(text) < 6:
        text = text.ljust(6, "0")
    try:
        r, g, b = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#ffffff"
    # Rec. 709 luma.
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return "#000000" if luma > 0.55 else "#ffffff"


__all__ = ["dominant_color", "dominant_from_image", "contrasting_text"]