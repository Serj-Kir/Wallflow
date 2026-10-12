"""Declarative animated scenes rendered with QPainter.

Scenes are the fallback live-wallpaper format: they need no browser engine and
run at a predictable cost on every platform. A ``.scene`` file is JSON:

.. code-block:: json

    {
      "type": "particles",
      "fps": 30,
      "background": {"type": "gradient", "colors": ["#0f1020", "#2b1055"], "angle": 45},
      "params": {"count": 160, "speed": 1.0, "hue": 210}
    }

Unknown or malformed fields fall back to defaults instead of failing the whole
wallpaper, so a scene keeps playing even if its file is a bit off.
"""

from __future__ import annotations

import json
import logging
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

MAX_FPS = 60
MIN_FPS = 1

BUILTIN_TYPES = (
    "particles",
    "starfield",
    "waves",
    "rain",
    "bubbles",
    "gradient",
    "plasma",
    "breathing",
    "grid",
)


@dataclass
class Background:
    kind: str = "solid"
    color: str = "#101018"
    colors: list[str] = field(default_factory=list)
    angle: float = 45.0

    @classmethod
    def from_dict(cls, data: Any) -> "Background":
        if not isinstance(data, dict):
            return cls()
        colors = data.get("colors") or []
        if isinstance(colors, str):
            colors = [colors]
        return cls(
            kind=str(data.get("type", "solid")),
            color=str(data.get("color", "#101018")),
            colors=[str(c) for c in colors][:8],
            angle=float(data.get("angle", 45.0)),
        )


@dataclass
class SceneSpec:
    """Validated, render-ready description of a scene."""

    name: str = "Scene"
    kind: str = "particles"
    fps: int = 30
    background: Background = field(default_factory=Background)
    params: dict[str, Any] = field(default_factory=dict)
    source: Optional[Path] = None

    @classmethod
    def load(cls, path: Path | str) -> "SceneSpec":
        p = Path(path)
        spec = cls(name=p.stem)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read scene %s: %s", p, exc)
            return spec
        if not isinstance(raw, dict):
            return spec

        spec.name = str(raw.get("name", p.stem))
        spec.kind = _one_of(raw.get("type"), BUILTIN_TYPES, "particles")
        spec.fps = _clamp_int(raw.get("fps"), MIN_FPS, MAX_FPS, 30)
        spec.background = Background.from_dict(raw.get("background"))
        params = raw.get("params")
        spec.params = dict(params) if isinstance(params, dict) else {}
        spec.source = p
        return spec

    @classmethod
    def builtin(cls, kind: str, *, name: str = "", **params: Any) -> "SceneSpec":
        """Build a spec from a built-in type plus ``params`` overrides."""
        return cls(
            name=name or kind.title(),
            kind=_one_of(kind, BUILTIN_TYPES, "particles"),
            fps=int(params.pop("fps", 30)),
            background=Background.from_dict(params.pop("background", None)),
            params=params,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.kind,
            "fps": self.fps,
            "background": {
                "type": self.background.kind,
                "color": self.background.color,
                "colors": self.background.colors,
                "angle": self.background.angle,
            },
            "params": self.params,
        }

    def save(self, path: Path | str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    def param(self, key: str, default: Any) -> Any:
        value = self.params.get(key, default)
        return type(default)(value) if isinstance(default, (int, float)) and not isinstance(default, bool) else value


def _one_of(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).lower() if value is not None else ""
    return text if text in allowed else default


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clamp_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


class SceneSimulator:
    """Pure-python simulation state; :class:`SceneWidget` only draws it.

    Keeping simulation separate from painting means the scene keeps running
    correctly when the window is hidden or the display is scaled, and makes the
    whole thing testable without a QApplication.
    """

    def __init__(self, spec: SceneSpec, width: int = 1920, height: int = 1080) -> None:
        self.spec = spec
        self.width = max(1, width)
        self.height = max(1, height)
        self.time = 0.0
        self._rng = random.Random(1337)
        self._particles: list[list[float]] = []
        self._seeded = False

    # ------------------------------------------------------------------ api
    def resize(self, width: int, height: int) -> None:
        width = max(1, width)
        height = max(1, height)
        if width == self.width and height == self.height:
            return
        self.width, self.height = width, height
        self._seeded = False

    def step(self, dt: float) -> None:
        dt = max(0.0, min(dt, 0.1))  # clamp huge gaps after a suspend
        self.time += dt
        if not self._seeded:
            self._seed()
        kind = self.spec.kind
        if kind == "particles":
            self._step_particles(dt)
        elif kind == "rain":
            self._step_rain(dt)
        elif kind == "bubbles":
            self._step_bubbles(dt)
        elif kind == "starfield":
            self._step_starfield(dt)

    def sample(self, time_seconds: Optional[float] = None) -> list[list[float]]:
        """Return the current particle/state rows for the renderer."""
        if not self._seeded:
            self._seed()
        return self._particles

    # -------------------------------------------------------------- seeding
    def _seed(self) -> None:
        kind = self.spec.kind
        count = self._count_for(kind)
        rng = self._rng
        rows: list[list[float]] = []
        for _ in range(count):
            if kind == "rain":
                rows.append([rng.uniform(0, self.width), rng.uniform(-self.height, self.height), rng.uniform(6, 18)])
            elif kind == "bubbles":
                rows.append(
                    [
                        rng.uniform(0, self.width),
                        rng.uniform(0, self.height),
                        rng.uniform(4, 26),
                        rng.uniform(0.6, 1.9),
                        rng.uniform(0.15, 0.6),
                    ]
                )
            elif kind == "starfield":
                rows.append(
                    [
                        rng.uniform(0, self.width),
                        rng.uniform(0, self.height),
                        rng.uniform(0.4, 2.2),
                        rng.uniform(12, 70),
                    ]
                )
            else:  # particles / waves share the generic layout
                rows.append(
                    [
                        rng.uniform(0, self.width),
                        rng.uniform(0, self.height),
                        rng.uniform(1.0, 3.4),
                        rng.uniform(0.2, 1.0),
                        rng.uniform(0, math.tau),
                    ]
                )
        self._particles = rows
        self._seeded = True

    def _count_for(self, kind: str) -> int:
        p = self.spec
        if kind == "rain":
            return _clamp_int(p.param("count", 220), 10, 900, 220)
        if kind == "bubbles":
            return _clamp_int(p.param("count", 40), 4, 200, 40)
        if kind == "starfield":
            return _clamp_int(p.param("count", 320), 20, 2000, 320)
        if kind == "waves":
            return _clamp_int(p.param("count", 7), 2, 24, 7)
        if kind == "grid":
            return _clamp_int(p.param("cells", 24), 4, 120, 24)
        return _clamp_int(p.param("count", 150), 5, 800, 150)

    # ---------------------------------------------------------------- steps
    def _step_particles(self, dt: float) -> None:
        speed = _clamp_float(self.spec.param("speed", 1.0), 0.05, 6.0, 1.0)
        drift = _clamp_float(self.spec.param("drift", 0.25), -3.0, 3.0, 0.25)
        connect = self.spec.param("connections", True)
        area = max(1.0, (self.width * self.height) / (1920.0 * 1080.0))
        radius = _clamp_float(self.spec.param("link_distance", 130), 40, 400, 130) * math.sqrt(area)
        for row in self._particles:
            row[0] += row[3] * speed * dt * 14
            row[1] += drift * speed * dt * 10
            row[4] += dt * 0.6
            if row[0] > self.width + 10:
                row[0] = -10
            elif row[0] < -10:
                row[0] = self.width + 10
            if row[1] > self.height + 10:
                row[1] = -10
            elif row[1] < -10:
                row[1] = self.height + 10
        self._connect_distance = radius if connect else 0.0

    def _step_rain(self, dt: float) -> None:
        speed = _clamp_float(self.spec.param("speed", 1.0), 0.1, 8.0, 1.0)
        slant = _clamp_float(self.spec.param("slant", 0.22), -1.0, 1.0, 0.22)
        for row in self._particles:
            row[1] += row[2] * speed * dt * 26
            row[0] += row[2] * speed * dt * 26 * slant
            if row[1] > self.height + 20:
                row[1] = -20
                row[0] = random.uniform(-self.width * 0.2, self.width)
                row[2] = random.uniform(6, 18)

    def _step_bubbles(self, dt: float) -> None:
        speed = _clamp_float(self.spec.param("speed", 1.0), 0.1, 5.0, 1.0)
        for row in self._particles:
            row[1] -= row[3] * speed * dt * 30
            row[0] += math.sin(self.time * row[4] + row[2]) * dt * 12
            if row[1] < -row[2] * 2:
                row[1] = self.height + row[2]
                row[0] = random.uniform(0, self.width)

    def _step_starfield(self, dt: float) -> None:
        speed = _clamp_float(self.spec.param("speed", 1.0), 0.05, 6.0, 1.0)
        for row in self._particles:
            row[0] -= row[3] * speed * dt
            if row[0] < -2:
                row[0] = self.width + 2

    # ------------------------------------------------------------- palettes
    def palette(self) -> list[tuple[int, int, int]]:
        """Resolve the scene's colours into opaque RGB tuples."""
        bg = self.spec.background
        raw: list[str] = []
        if bg.colors:
            raw = bg.colors
        elif bg.color:
            raw = [bg.color]
        out: list[tuple[int, int, int]] = []
        for color in raw[:8]:
            parsed = parse_color(color)
            if parsed:
                out.append(parsed)
        return out or [(16, 16, 24)]


def parse_color(value: str) -> Optional[tuple[int, int, int]]:
    """Parse ``#rgb``, ``#rrggbb``, ``#rrggbbaa`` or ``rgb(r,g,b)``."""
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("#"):
        digits = text[1:]
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        if len(digits) >= 6:
            try:
                return (
                    int(digits[0:2], 16),
                    int(digits[2:4], 16),
                    int(digits[4:6], 16),
                )
            except ValueError:
                return None
        return None
    match = re_match_rgb(text)
    if match:
        try:
            return (int(match[0]), int(match[1]), int(match[2]))
        except ValueError:
            return None
    return None


def re_match_rgb(text: str):
    import re

    match = re.match(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", text, re.IGNORECASE)
    return match.groups() if match else None


def hsv_color(hue: float, saturation: float, value: float, alpha: float = 255) -> tuple[int, int, int, int]:
    """HSV -> RGBA. Hue is in degrees and wraps automatically."""
    hue = hue % 360.0
    c = value * saturation
    x = c * (1 - abs((hue / 60.0) % 2 - 1))
    m = value - c
    sector = int(hue // 60) % 6
    r, g, b = [
        (c, x, 0.0), (x, c, 0.0), (0.0, c, x),
        (0.0, x, c), (x, 0.0, c), (c, 0.0, x),
    ][sector]
    return (
        int((r + m) * 255), int((g + m) * 255), int((b + m) * 255), int(max(0, min(1, alpha)) * 255),
    )


__all__ = [
    "SceneSpec",
    "SceneSimulator",
    "Background",
    "BUILTIN_TYPES",
    "parse_color",
    "hsv_color",
]