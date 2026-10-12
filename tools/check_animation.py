"""Compare two screenshots to prove the desktop wallpaper is actually animating.

Compares a vertical strip of the left edge, which is always plain desktop and
never covered by an application window.
"""

import sys

from PySide6.QtGui import QImage


def load(path: str) -> QImage:
    image = QImage(path)
    if image.isNull():
        raise SystemExit(f"не удалось прочитать {path}")
    return image


def main() -> int:
    a, b = load(sys.argv[1]), load(sys.argv[2])
    if a.size() != b.size():
        raise SystemExit(f"разный размер: {a.size()} vs {b.size()}")

    # Strip on the left edge, below the panel, above the taskbar.
    x0, y0, x1, y1 = 4, 220, 44, 950
    width, height = x1 - x0, y1 - y0

    changed = 0
    total = 0
    colours_a: set[int] = set()
    colours_b: set[int] = set()

    for y in range(y0, y1):
        for x in range(x0, x1):
            pa = a.pixel(x, y)
            pb = b.pixel(x, y)
            total += 1
            colours_a.add(pa)
            colours_b.add(pb)
            if pa != pb:
                changed += 1

    percent = changed / total * 100 if total else 0
    print(f"область           : {width}x{height} ({total} пикселей)")
    print(f"различных цветов A: {len(colours_a)}")
    print(f"различных цветов B: {len(colours_b)}")
    print(f"изменилось пикселей: {changed} ({percent:.2f}%)")
    if percent > 0.5 and len(colours_a) > 20:
        print("ВЕРДИКТ: анимация идёт, фон не однотонный")
        return 0
    if len(colours_a) <= 20:
        print("ВЕРДИКТ: фон однотонный — анимации нет")
        return 1
    print("ВЕРДИКТ: фон разноцветный, но неподвижный")
    return 1


if __name__ == "__main__":
    sys.exit(main())