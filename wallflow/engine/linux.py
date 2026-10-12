"""Linux wallpaper backends.

Wallflow targets the desktops people actually run: GNOME/KDE/XFCE/Cinnamon/MATE/
LXQt under X11 or Wayland, plus the standalone tiling compositors Sway and
Hyprland. Each backend talks to the mechanism that desktop provides; where one
exists that also handles per-monitor placement, we prefer it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .. import utils
from ..models import MonitorInfo
from .base import (
    BackendCapabilities,
    FitMode,
    WallpaperBackend,
    WallpaperRequest,
)

log = logging.getLogger(__name__)

# GNOME picture-options values keyed by our FitMode.
_GNOME_FIT = {
    FitMode.FILL: "zoom",
    FitMode.FIT: "scaled",
    FitMode.STRETCH: "stretched",
    FitMode.CENTER: "centered",
    FitMode.TILE: "wallpaper",
}

_XFCE_STYLE = {
    FitMode.FILL: "fill",
    FitMode.FIT: "fit",
    FitMode.STRETCH: "stretched",
    FitMode.CENTER: "centered",
    FitMode.TILE: "tiled",
}


def _uri(path: Path) -> str:
    return Path(path).resolve().as_uri()


class LinuxBackendBase(WallpaperBackend):
    """Shared plumbing: display-name helpers and a spot for monitor lists."""

    label = "Linux"

    def __init__(self) -> None:
        super().__init__()
        self._monitors: list[MonitorInfo] = []

    def monitors(self) -> list[MonitorInfo]:
        return list(self._monitors)

    def set_monitors(self, monitors: list[MonitorInfo]) -> None:
        self._monitors = monitors

    @staticmethod
    def monitor_names() -> list[str]:
        """Output names as reported by the compositor, e.g. ``DP-1``."""
        try:
            proc = utils.run(["wlr-randr", "--output"], timeout=5)
            if proc.returncode == 0:
                return [line.split()[0] for line in proc.stdout.splitlines() if line.strip()]
        except Exception:  # noqa: BLE001
            pass
        try:
            proc = utils.run(["xrandr", "--query"], timeout=5)
            if proc.returncode == 0:
                names = []
                for line in proc.stdout.splitlines():
                    if line.startswith(("Screen", "Provider")):
                        continue
                    if " connected" in line or line.strip().endswith(" disconnected"):
                        names.append(line.split()[0])
                return [n for n in names if n != "default"]
        except Exception:  # noqa: BLE001
            pass
        return []

    @staticmethod
    def primary_monitor_name() -> str:
        try:
            proc = utils.run(["wlr-randr", "--output"], timeout=5)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if "(primary)" in line:
                        return line.split()[0]
        except Exception:  # noqa: BLE001
            pass
        try:
            proc = utils.run(["xrandr", "--query"], timeout=5)
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if " primary " in line:
                        return line.split()[0]
        except Exception:  # noqa: BLE001
            pass
        return ""


# --------------------------------------------------------------------------
# GNOME, Cinnamon, MATE, Budgie — all share the org.gnome.desktop.background
# schema, so one backend covers them.
# --------------------------------------------------------------------------
class GnomeBackend(LinuxBackendBase):
    name = "gnome"
    label = "GNOME / Cinnamon / MATE"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(
            per_monitor=False,  # GNOME keeps exactly one wallpaper for all screens
            accent_color=False,
        )

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None:
            return False
        if not utils.which("gsettings"):
            return False
        uri = _uri(request.path)
        ok = True
        ok &= self._gset("org.gnome.desktop.background", "picture-uri", uri)
        # GNOME 42+ honours a separate image in dark mode.
        ok &= self._gset("org.gnome.desktop.background", "picture-uri-dark", uri)
        ok &= self._gset(
            "org.gnome.desktop.background",
            "picture-options",
            _GNOME_FIT.get(request.fit, "zoom"),
        )
        return ok

    def clear(self) -> bool:
        if not utils.which("gsettings"):
            return False
        return (
            self._gset("org.gnome.desktop.background", "picture-uri", "")
            and self._gset("org.gnome.desktop.background", "picture-uri-dark", "")
        )

    @staticmethod
    def _gset(schema: str, key: str, value: str) -> bool:
        proc = utils.run(["gsettings", "set", schema, key, value])
        if proc.returncode != 0:
            log.debug("gsettings set %s %s failed: %s", schema, key, proc.stderr.strip())
            return False
        return True


class GnomeDconfBackend(GnomeBackend):
    """Fallback for setups where the gsettings CLI is missing."""

    name = "gnome-dconf"
    label = "GNOME (dconf)"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=False)

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("dconf"):
            return False
        base = "/org/gnome/desktop/background/"
        uri = _uri(request.path)
        ok = True
        for key, value in (
            ("picture-uri", uri),
            ("picture-uri-dark", uri),
            ("picture-options", _GNOME_FIT.get(request.fit, "zoom")),
        ):
            proc = utils.run(["dconf", "write", base + key, value])
            ok &= proc.returncode == 0
        return ok

    def clear(self) -> bool:
        if not utils.which("dconf"):
            return False
        return utils.run(["dconf", "write", "/org/gnome/desktop/background/picture-uri", "''"]).returncode == 0


# --------------------------------------------------------------------------
# KDE Plasma
# --------------------------------------------------------------------------
_KDE_JS = """
var allDesktops = desktops();
var uri = "%(uri)s";
for (var i = 0; i < allDesktops.length; i++) {
    var d = allDesktops[i];
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
    d.writeConfig("Image", uri);
}
"""

_KDE_JS_SINGLE = """
var allDesktops = desktops();
var d = allDesktops[%(index)d];
if (d) {
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = Array("Wallpaper", "org.kde.image", "General");
    d.writeConfig("Image", "%(uri)s");
}
"""

_KDE_COLOR_JS = """
var allDesktops = desktops();
for (var i = 0; i < allDesktops.length; i++) {
    allDesktops[i].color = "%(color)s";
}
"""


class PlasmaBackend(LinuxBackendBase):
    name = "plasma"
    label = "KDE Plasma"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(
            per_monitor=True, accent_color=True
        )

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None:
            return False
        uri = _uri(request.path)

        # plasma-apply-wallpaperimage (Plasma 5.20+, still present in 6.x) is
        # the least intrusive path and applies to every screen.
        binary = utils.which("plasma-apply-wallpaperimage")
        if binary:
            proc = utils.run([binary, str(Path(request.path).resolve())])
            if proc.returncode == 0:
                return True
            log.debug("plasma-apply-wallpaperimage failed: %s", proc.stderr.strip())

        if request.monitor_id and self._monitor_names():
            index = self._monitor_names().index(request.monitor_id)
            js = _KDE_JS_SINGLE % {"uri": uri, "index": index}
        else:
            js = _KDE_JS % {"uri": uri}
        return self._eval(js)

    def clear(self) -> bool:
        return self._eval(_KDE_COLOR_JS % {"color": "#000000"})

    def set_accent_color(self, hex_color: str) -> bool:
        return self._eval(_KDE_COLOR_JS % {"color": self.normalise_hex(hex_color)})

    def _monitor_names(self) -> list[str]:
        names = super().monitor_names()
        return names or [m.id for m in self._monitors]

    @staticmethod
    def _eval(js: str) -> bool:
        candidates = [
            ["qdbus6", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", js],
            ["qdbus", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", js],
        ]
        for argv in candidates:
            if not utils.which(argv[0]):
                continue
            proc = utils.run(argv)
            if proc.returncode == 0 and "error" not in proc.stdout.lower():
                return True
            log.debug("%s evaluateScript failed: %s", argv[0], proc.stderr.strip())
        return False


# --------------------------------------------------------------------------
# XFCE
# --------------------------------------------------------------------------
class XfceBackend(LinuxBackendBase):
    name = "xfce"
    label = "Xfce"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=True)

    def _channel_base(self, monitor_id: str, prop: str) -> list[str]:
        ids = self.monitor_names()
        index = ids.index(monitor_id) if monitor_id in ids else 0
        return ["xfconf-query", "-c", "xfce4-desktop", "-p", f"/backdrop/screen0/monitor{index}/workspace0/{prop}"]

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("xfconf-query"):
            return False
        base = self._channel_base(request.monitor_id, "")
        ok = self._set(base + ["last-image"], str(Path(request.path).resolve()))
        ok &= self._set(base + ["image-style"], _XFCE_STYLE.get(request.fit, "fill"))
        return ok

    def clear(self) -> bool:
        if not utils.which("xfconf-query"):
            return False
        base = ["xfconf-query", "-c", "xfce4-desktop", "-p", "/backdrop/screen0/monitor0/workspace0/last-image"]
        return self._set(base, "")

    @staticmethod
    def _set(argv: list[str], value: str) -> bool:
        proc = utils.run(argv + ["-s", value])
        if proc.returncode != 0:
            log.debug("xfconf-query failed: %s", proc.stderr.strip())
            return False
        return True


# --------------------------------------------------------------------------
# Sway
# --------------------------------------------------------------------------
class SwayBackend(LinuxBackendBase):
    name = "sway"
    label = "Sway"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(
            per_monitor=True,
            fit_modes=("fill", "fit", "center", "stretch"),
            accent_color=True,
        )

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("swaymsg"):
            return False
        target = request.monitor_id or "*"
        mode = {
            FitMode.FILL: "fill",
            FitMode.FIT: "fit",
            FitMode.CENTER: "center",
            FitMode.STRETCH: "stretch",
        }.get(request.fit, "fill")
        argv = ["swaymsg", "output", target, "bg", str(Path(request.path).resolve()), mode]
        proc = utils.run(argv)
        return proc.returncode == 0

    def clear(self) -> bool:
        if not utils.which("swaymsg"):
            return False
        return utils.run(["swaymsg", "output", "*", "bg", "#000000", "solid_color"]).returncode == 0

    def set_accent_color(self, hex_color: str) -> bool:
        proc = utils.run(
            ["swaymsg", "output", "*", "bg", self.normalise_hex(hex_color), "solid_color"]
        )
        return proc.returncode == 0


# --------------------------------------------------------------------------
# Hyprland
# --------------------------------------------------------------------------
class HyprlandBackend(LinuxBackendBase):
    name = "hyprland"
    label = "Hyprland"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=True)

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("hyprctl"):
            return False
        value = str(Path(request.path).resolve())
        # Hyprland >= 0.40 accepts an optional monitor argument.
        if request.monitor_id:
            proc = utils.run(["hyprctl", "wallpaper", "set", value + f",{request.monitor_id}"])
            if proc.returncode == 0:
                return True
        proc = utils.run(["hyprctl", "wallpaper", "set", value])
        return proc.returncode == 0

    def clear(self) -> bool:
        if not utils.which("hyprctl"):
            return False
        return utils.run(["hyprctl", "wallpaper", "set", ""]).returncode == 0


# --------------------------------------------------------------------------
# X11 one-offs: feh / xsetroot
# --------------------------------------------------------------------------
class X11FehBackend(LinuxBackendBase):
    name = "feh"
    label = "feh (X11)"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(
            per_monitor=False, fit_modes=("fill", "fit", "stretch", "center", "tile")
        )

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("feh"):
            return False
        flag = {
            FitMode.FILL: "--bg-fill",
            FitMode.FIT: "--bg-fit",
            FitMode.STRETCH: "--bg-scale",
            FitMode.CENTER: "--bg-center",
            FitMode.TILE: "--bg-tile",
        }.get(request.fit, "--bg-fill")
        proc = utils.run(["feh", "--no-fehbg", flag, str(Path(request.path).resolve())])
        return proc.returncode == 0

    def clear(self) -> bool:
        if not utils.which("xsetroot"):
            return False
        return utils.run(["xsetroot", "-solid", "#000000"]).returncode == 0


class X11XsetrootBackend(LinuxBackendBase):
    name = "xsetroot"
    label = "xsetroot (X11)"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=False, fit_modes=("stretch",))

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None or not utils.which("xsetroot"):
            return False
        if request.fit in (FitMode.FILL, FitMode.FIT):
            return False
        proc = utils.run(["xsetroot", "-bitmap", str(Path(request.path).resolve())])
        return proc.returncode == 0

    def clear(self) -> bool:
        if not utils.which("xsetroot"):
            return False
        return utils.run(["xsetroot", "-solid", "#000000"]).returncode == 0


# --------------------------------------------------------------------------
# LXQt
# --------------------------------------------------------------------------
class LxqtBackend(LinuxBackendBase):
    name = "lxqt"
    label = "LXQt"

    def __init__(self) -> None:
        super().__init__()
        self.capabilities = BackendCapabilities(per_monitor=True)

    def set_wallpaper(self, request: WallpaperRequest) -> bool:
        if request.path is None:
            return False
        # LXQt keeps its wallpaper in a plain INI file per screen.
        config = Path.home() / ".config" / "pcmanfm-qt" / "wallpaper"
        try:
            config.parent.mkdir(parents=True, exist_ok=True)
            existing = config.read_text(encoding="utf-8") if config.exists() else ""
        except OSError:
            return False
        uri = _uri(request.path)
        if f"{request.monitor_id}/{uri}" in existing:
            return True
        line = f"{request.monitor_id or 'Screen-1'}/{uri}"
        text = existing.rstrip("\n")
        text = f"{text}\n{line}" if text else line
        try:
            config.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            log.debug("LXQt wallpaper write failed: %s", exc)
            return False
        utils.run(["pcmanfm-qt", "--reconfigure"], timeout=5)
        return True

    def clear(self) -> bool:
        config = Path.home() / ".config" / "pcmanfm-qt" / "wallpaper"
        try:
            if config.exists():
                config.write_text("", encoding="utf-8")
        except OSError:
            return False
        utils.run(["pcmanfm-qt", "--reconfigure"], timeout=5)
        return True


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
_PREFERENCE: dict[str, type[WallpaperBackend]] = {
    "sway": SwayBackend,
    "hyprland": HyprlandBackend,
    "kde": PlasmaBackend,
    "plasma": PlasmaBackend,
    "xfce": XfceBackend,
    "lxqt": LxqtBackend,
    "cinnamon": GnomeBackend,
    "mate": GnomeBackend,
    "budgie": GnomeBackend,
    "gnome": GnomeBackend,
}


def detect_backends(desktop: str | None = None) -> list[WallpaperBackend]:
    """Return every backend that looks usable on this machine, best first."""
    desktop = (desktop or utils.desktop_environment()).lower()
    session = utils.session_type()
    candidates: list[WallpaperBackend] = []

    preferred = _PREFERENCE.get(desktop)
    if preferred is not None:
        candidates.append(preferred())

    if desktop in ("gnome", "cinnamon", "mate", "budgie", "unity", "unknown", ""):
        candidates.append(GnomeDconfBackend())

    if session == "x11":
        candidates.append(X11FehBackend())
        candidates.append(X11XsetrootBackend())

    if not candidates:
        candidates.append(GnomeBackend())
    return candidates


__all__ = [
    "GnomeBackend",
    "GnomeDconfBackend",
    "PlasmaBackend",
    "XfceBackend",
    "SwayBackend",
    "HyprlandBackend",
    "X11FehBackend",
    "X11XsetrootBackend",
    "LxqtBackend",
    "detect_backends",
]