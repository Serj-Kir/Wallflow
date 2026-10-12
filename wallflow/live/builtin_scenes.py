"""Wallpaper builders that ship with Wallflow.

Each scene is a :class:`~wallflow.live.scene.SceneSpec` you can drop into the
library as a ``.scene`` file.
"""

from __future__ import annotations

from .scene import SceneSpec

#: name -> spec, in the order they appear in the "create scene" dialog.
BUILTIN_SCENES: dict[str, SceneSpec] = {
    spec.name: spec
    for spec in (
        SceneSpec.builtin(
            "particles",
            name="Частицы",
            fps=30,
            count=140,
            speed=0.9,
            hue=210,
            saturation=0.65,
            background={"type": "gradient", "colors": ["#0b1020", "#131a33"], "angle": 60},
        ),
        SceneSpec.builtin(
            "starfield",
            name="Звёздное небо",
            fps=30,
            count=280,
            speed=1.0,
            background={"type": "gradient", "colors": ["#02030a", "#0a1030"], "angle": 90},
        ),
        SceneSpec.builtin(
            "waves",
            name="Волны",
            fps=30,
            count=8,
            hue=195,
            amplitude=70,
            wavelength=460,
            background={"type": "gradient", "colors": ["#04121f", "#0a2a44"], "angle": 30},
        ),
        SceneSpec.builtin(
            "bubbles",
            name="Пузыри",
            fps=30,
            count=36,
            speed=0.8,
            background={"type": "gradient", "colors": ["#081426", "#123a5c"], "angle": 45},
        ),
        SceneSpec.builtin(
            "rain",
            name="Дождь",
            fps=30,
            count=200,
            speed=1.1,
            hue=205,
            background={"type": "solid", "color": "#0a0e16"},
        ),
        SceneSpec.builtin(
            "plasma",
            name="Плазма",
            fps=24,
            hue=270,
            scale=3.2,
            cell=16,
            background={"type": "solid", "color": "#05050c"},
        ),
        SceneSpec.builtin(
            "breathing",
            name="Дыхание",
            fps=24,
            speed=0.7,
            background={"type": "gradient", "colors": ["#1a0b2e", "#2b1055"], "angle": 60},
        ),
        SceneSpec.builtin(
            "grid",
            name="Сетка",
            fps=30,
            hue=160,
            cells=26,
            speed=0.6,
            background={"type": "solid", "color": "#04080c"},
        ),
    )
}


def write_scene(name: str, destination) -> str:
    """Write a builtin scene to ``destination`` and return the path."""
    spec = BUILTIN_SCENES[name]
    return str(spec.save(destination))


__all__ = ["BUILTIN_SCENES", "write_scene"]