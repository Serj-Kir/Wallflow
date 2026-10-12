"""Generate sample wallpapers so the app has something to show on first run.

Creates a handful of gradient/pattern images plus a small HTML live wallpaper
and a scene file, then imports them into the library.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QRadialGradient,
)

WIDTH, HEIGHT = 1920, 1080

PRESETS = [
    ("Закат над морем", ["#ff9a3c", "#ff5f6d", "#6a3093"], "vertical"),
    ("Северное сияние", ["#00c9a7", "#0b486b", "#1a0b3d"], "vertical"),
    ("Ночной город", ["#0f2027", "#203a43", "#2c5364"], "vertical"),
    ("Лесная глубина", ["#134e5e", "#71b280", "#2f7336"], "vertical"),
    ("Космос", ["#0b1026", "#3a1c71", "#d76d77"], "radial"),
    ("Пустыня", ["#f7b733", "#fc4a1a", "#7a1f2b"], "radial"),
    ("Зимний пейзаж", ["#e6f0ff", "#a8c8e8", "#5a7fa8"], "vertical"),
    ("Аврора", ["#12043a", "#2b1055", "#7597de"], "radial"),
]


def make_image(name: str, colors: list[str], kind: str) -> QImage:
    image = QImage(WIDTH, HEIGHT, QImage.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    start, middle, end = (QColor(c) for c in colors)
    if kind == "radial":
        gradient = QRadialGradient(QPointF(WIDTH * 0.35, HEIGHT * 0.3), WIDTH * 0.85)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(0.55, middle)
        gradient.setColorAt(1.0, end)
    else:
        gradient = QLinearGradient(0, 0, WIDTH * 0.25, HEIGHT)
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(0.5, middle)
        gradient.setColorAt(1.0, end)
    painter.fillRect(0, 0, WIDTH, HEIGHT, gradient)

    # A few soft blobs so the images read as wallpaper rather than a flat fill.
    painter.setPen(Qt.NoPen)
    for index in range(5):
        blob = QRadialGradient(
            QPointF(WIDTH * (0.12 + 0.19 * index), HEIGHT * (0.2 + 0.15 * (index % 3))),
            WIDTH * 0.22,
        )
        glow = QColor(255, 255, 255, 26)
        blob.setColorAt(0.0, glow)
        blob.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(blob)
        painter.drawRect(0, 0, WIDTH, HEIGHT)

    painter.end()
    return image


def make_html_project(root: Path) -> None:
    """A tiny animated HTML wallpaper used to demo the Web renderer."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Пульс</title>
<style>
  html,body{margin:0;height:100%;background:#060814;overflow:hidden;
    font-family:system-ui,sans-serif;}
  canvas{display:block}
  .t{position:fixed;left:0;right:0;bottom:6vh;text-align:center;color:#8fa6d8;
     font-size:14px;letter-spacing:.35em;text-transform:uppercase;opacity:.7}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="t">wallflow&nbsp;·&nbsp;html wallpaper</div>
<script>
const c=document.getElementById('c'),x=c.getContext('2d');
let w,h,dots=[];
function size(){w=c.width=innerWidth*devicePixelRatio;h=c.height=innerHeight*devicePixelRatio;}
addEventListener('resize',()=>{size();seed();});
function seed(){dots=Array.from({length:90},()=>({
  x:Math.random()*w,y:Math.random()*h,r:1+Math.random()*2.6,
  vx:(Math.random()-.5)*.35,vy:(Math.random()-.5)*.35,hue:190+Math.random()*110}));}
function frame(){
  x.fillStyle='rgba(6,8,20,.14)';x.fillRect(0,0,w,h);
  for(const d of dots){
    d.x+=d.vx;d.y+=d.vy;
    if(d.x<0||d.x>w)d.vx*=-1;
    if(d.y<0||d.y>h)d.vy*=-1;
    x.beginPath();
    x.fillStyle='hsla('+d.hue+',90%,68%,.85)';
    x.arc(d.x,d.y,d.r,0,6.284);
    x.fill();
    x.beginPath();
    x.strokeStyle='hsla('+d.hue+',90%,62%,.10)';
    x.lineWidth=1;
    for(const e of dots){
      const dx=e.x-d.x,dy=e.y-d.y,d2=dx*dx+dy*dy;
      if(d2<14000){x.moveTo(d.x,d.y);x.lineTo(e.x,e.y);}
    }
    x.stroke();
  }
  requestAnimationFrame(frame);
}
size();seed();frame();
</script>
</body>
</html>
""",
        encoding="utf-8",
    )
    (root / "project.json").write_text(
        '{"title":"Пульс","author":"Wallflow","description":"Живая сеть частиц на HTML5"}',
        encoding="utf-8",
    )


def main() -> int:
    import shutil
    import tempfile

    from wallflow import paths
    from wallflow.library import Library
    from wallflow.live.builtin_scenes import BUILTIN_SCENES

    paths.ensure_dirs()

    # Content is staged outside the library first: import_dir() copies files
    # in, so generating them directly inside would duplicate everything.
    staging = Path(tempfile.mkdtemp(prefix="wallflow-demo-"))

    for name, colors, kind in PRESETS:
        image = make_image(name, colors, kind)
        path = staging / f"{name}.jpg"
        image.save(str(path), "JPG", 90)
        print(f"  {path.name}")

    web_root = staging / "Пульс (HTML)"
    make_html_project(web_root)
    print(f"  {web_root.name}/")

    for scene_name in ("Частицы", "Звёздное небо", "Волны"):
        spec = BUILTIN_SCENES[scene_name]
        path = staging / f"{scene_name}.scene"
        spec.save(path)
        print(f"  {path.name}")

    library = Library()
    added = library.import_dir(staging)
    shutil.rmtree(staging, ignore_errors=True)
    print(f"\nДобавлено: {added}")
    print(f"В библиотеке: {len(library)} обоев")
    print(f"Папка: {paths.wallpaper_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())