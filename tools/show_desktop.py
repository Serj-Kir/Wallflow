"""Hide every top-level window so the desktop (and its live wallpaper) is visible.

Cinnamon's window manager ignores ``wmctrl -b hidden``, so windows are unmapped
through Xlib instead. Run with ``--restore`` to map them back.
"""

import ctypes
import subprocess
import sys

_STATE = "/tmp/wallflow-hidden-windows"
_ignore = "wallpaper layer"


def _x11():
    x = ctypes.CDLL("libX11.so.6")
    err = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    x.XSetErrorHandler.argtypes = [err]
    x.XSetErrorHandler.restype = ctypes.c_void_p
    keep = err(lambda d, e: 0)
    x.XSetErrorHandler(keep)
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XUnmapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XFlush.argtypes = [ctypes.c_void_p]
    display = x.XOpenDisplay(None)
    if not display:
        raise SystemExit("нет доступа к X-серверу")
    return x, display, keep


def _list() -> list[tuple[str, str]]:
    out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        rows.append((parts[0], parts[4] if len(parts) > 4 else ""))
    return rows


def hide() -> int:
    x, display, _keep = _x11()
    hidden = []
    for wid, title in _list():
        if _ignore in title or "Рабочий стол" in title:
            continue
        x.XUnmapWindow(display, int(wid, 16))
        hidden.append((wid, title))
    x.XSync(display, 0)
    x.XFlush(display)
    with open(_STATE, "w", encoding="utf-8") as fh:
        for wid, title in hidden:
            fh.write(f"{wid}\t{title}\n")
    print(f"скрыто окон: {len(hidden)}")
    return 0


def restore() -> int:
    x, display, _keep = _x11()
    try:
        rows = [line.rstrip("\n").split("\t", 1) for line in open(_STATE, encoding="utf-8")]
    except OSError:
        print("нечего восстанавливать")
        return 0
    for wid, _title in rows:
        x.XMapWindow(display, int(wid, 16))
    x.XSync(display, 0)
    x.XFlush(display)
    print(f"восстановлено окон: {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(restore() if "--restore" in sys.argv else hide())