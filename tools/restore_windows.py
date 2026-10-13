"""Re-map windows that were unmapped by ``show_desktop.py``.

Cinnamon drops a window from its client list as soon as it is unmapped, so
``wmctrl -l`` can no longer find it. The X server still has the window, so this
walks the root's children and maps the ones with a real geometry back.
"""

import ctypes
import re
import subprocess
import sys

# A child line looks like:
#   0x4600003 "Title": ("Class" "Class2")  1281x909+50+50  +50+50
# The title may contain spaces, quotes and colons, so the geometry is matched
# on the tail of the line and the window id is taken from the head.
GEOMETRY = re.compile(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)")
WINDOW_ID = re.compile(r"^\s*(0x[0-9a-fA-F]+)")
CLASS = re.compile(r'\("([^"]*)"\s+"([^"]*)"\)')

SKIP_CLASSES = {"nemo-desktop", "cinnamon"}


def parse(line: str):
    id_match = WINDOW_ID.match(line)
    geo_match = GEOMETRY.search(line)
    if not id_match or not geo_match:
        return None
    class_match = CLASS.search(line)
    classes = " ".join(class_match.groups()) if class_match else ""
    w, h, x, y = (int(v) for v in geo_match.groups())
    return id_match.group(1), classes, w, h, x, y


def main() -> int:
    out = subprocess.run(["xwininfo", "-root", "-children"],
                         capture_output=True, text=True).stdout

    x = ctypes.CDLL("libX11.so.6")
    err = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    x.XSetErrorHandler.argtypes = [err]
    x.XSetErrorHandler.restype = ctypes.c_void_p
    keep = err(lambda d, e: 0)   # never abort on a vanished window
    x.XSetErrorHandler(keep)
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    display = x.XOpenDisplay(None)

    mapped = []
    for line in out.splitlines():
        parsed = parse(line)
        if parsed is None:
            continue
        wid, classes, w, h, xpos, ypos = parsed
        if any(c in classes for c in SKIP_CLASSES):
            continue
        if "wallpaper layer" in line:
            continue
        if w < 200 or h < 200:
            continue          # helper/utility windows stay hidden
        if (xpos, ypos) in ((-1, -1), (-99, -99), (-200, -200)):
            continue
        x.XMapWindow(display, int(wid, 16))
        title = line.split('"')[1] if '"' in line else classes
        mapped.append((wid, title, w, h))

    x.XSync(display, 0)
    for wid, title, w, h in mapped:
        print(f"  {wid}  {w}x{h}  {title[:60]}")
    print(f"восстановлено окон: {len(mapped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())