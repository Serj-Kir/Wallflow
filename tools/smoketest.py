"""Headless smoke test: builds every screen and exercises the core logic.

Run with ``QT_QPA_PLATFORM=offscreen python tools/smoketest.py``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WALLFLOW_DATA_DIR", tempfile.mkdtemp(prefix="wallflow-smoke-"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(f"{name}: {detail}")


def section(title: str) -> None:
    print(f"\n{title}")


def main() -> int:
    QApplication.instance() or QApplication(sys.argv[:1])
    app = QApplication.instance()

    from wallflow import autostart, hotkeys, paths
    from wallflow.config import Config
    from wallflow.controller import PlaybackController
    from wallflow.engine import create_stack
    from wallflow.library import Library
    from wallflow.live import capabilities, create_widget
    from wallflow.live.builtin_scenes import BUILTIN_SCENES
    from wallflow.live.scene import SceneSimulator, SceneSpec
    from wallflow.models import Playlist, Profile, ScheduleRule, Wallpaper, WallpaperKind
    from wallflow.player import WallpaperPlayer
    from wallflow.ui import theme
    from wallflow.ui.color import contrasting_text, dominant_color

    section("Paths and config")
    check("data dir created", paths.data_dir().is_dir(), str(paths.data_dir()))
    config = Config()
    config.set("max_fps", 45)
    check("config round-trips", Config().get("max_fps") == 45)
    check("defaults preserved", config.get("thumb_size") == 220)

    section("Library")
    library = Library()
    check("starts empty", len(library) == 0)

    tmp = Path(paths.data_dir()).parent / "sample"
    tmp.mkdir(parents=True, exist_ok=True)
    for index, colour in enumerate(("#3355aa", "#aa3355", "#33aa55", "#aa8833")):
        image = QImage(640, 360, QImage.Format_RGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.fillRect(0, 0, 640, 360, Qt.GlobalColor.transparent)
        from PySide6.QtGui import QColor

        painter.fillRect(0, 0, 640, 360, QColor(colour))
        painter.end()
        image.save(str(tmp / f"sample{index}.jpg"))

    added = library.import_dir(tmp)
    check("imported sample images", added == 4, f"got {added}")
    check("library indexed", len(library) == 4, f"got {len(library)}")
    first = library.all()[0]
    check("kind detected", first.wallpaper_kind is WallpaperKind.IMAGE, first.kind)
    check("favourite toggle", library.toggle_favourite(first.id) and first.favourite)
    check("filter by favourite", len(library.filtered(favourites_only=True)) == 1)
    check("search works", len(library.filtered(search="sample1")) >= 1)

    section("Accent colour")
    colour = dominant_color(Path(first.path))
    check("accent extracted", colour.startswith("#") and len(colour) == 7, colour)
    check("contrast is sane", contrasting_text(colour) in ("#000000", "#ffffff"))

    section("Scene engine")
    for name, spec in BUILTIN_SCENES.items():
        simulator = SceneSimulator(spec, 1920, 1080)
        simulator.step(0.016)
        simulator.step(0.016)
        rows = simulator.sample()
        check(f"scene '{name}' simulates", len(rows) > 0, f"{len(rows)} rows")

    scene_path = BUILTIN_SCENES["Частицы"].save(Path(paths.data_dir()) / "particles.scene")
    loaded = SceneSpec.load(scene_path)
    check("scene loads from disk", loaded.kind == "particles", loaded.kind)
    check("scene fps kept", loaded.fps == 30, str(loaded.fps))

    section("Widgets")
    kinds = capabilities()
    check("all formats available", all(kinds.values()), str(kinds))
    for kind, widget_type in (
        (WallpaperKind.IMAGE, "ImageWidget"),
        (WallpaperKind.SCENE, "SceneWidget"),
        (WallpaperKind.VIDEO, "VideoWidget"),
        (WallpaperKind.WEB, "WebWidget"),
    ):
        widget = create_widget_for_kind(kind)
        check(f"{kind.value} widget builds", widget is not None and type(widget).__name__ == widget_type)

    section("Backends and player")
    backend = create_stack()
    check("backend chosen", backend.primary is not None, backend.primary.name)
    check("capabilities exist", hasattr(backend.capabilities, "per_monitor"))

    player = WallpaperPlayer(backend, config)
    player.refresh_monitors()
    check("monitors discovered", len(player.monitors()) >= 1, f"{len(player.monitors())}")

    controller = PlaybackController(config, library, player)
    controller.start()
    check("profile active", controller.active_profile is not None)
    check("playlists listable", isinstance(controller.playlists, list))

    section("Playlists and rules")
    playlist = controller.add_playlist("Тест", [first.id])
    check("playlist created", len(playlist) == 1)
    controller.set_playlist(playlist.id)
    check("playlist active", controller.active_playlist is not None)
    controller.next()
    check("next() survives single-item list", controller.active_playlist is not None)

    rule = ScheduleRule(name="Тест", profile_id=controller.active_profile.id,
                        weekdays=[], start_minutes=0, end_minutes=24 * 60)
    check("rule matches all day", rule.matches(700, 3))
    night = ScheduleRule(weekdays=[], start_minutes=22 * 60, end_minutes=3 * 60)
    check("wrap-around rule matches", night.matches(23 * 60, 3))
    check("wrap-around rule excludes noon", not night.matches(12 * 60, 3))

    section("Pause policies")
    controller.set_manual_pause(True)
    check("manual pause", controller.paused)
    controller.set_manual_pause(False)
    check("resume clears pause", not controller.paused)

    section("Autostart and hotkeys")
    enabled = autostart.is_enabled()
    check("autostart path resolvable", isinstance(enabled, bool))
    supported, reason = hotkeys.global_hotkeys_supported()
    check("hotkey support probed", isinstance(supported, bool), reason)
    manager = hotkeys.HotkeyManager()
    manager.register({"Тест": ("Ctrl+Alt+F9", lambda: None)}, 0)
    check("hotkey registration did not crash", isinstance(manager.failed, set))
    manager.unregister()

    section("UI construction")
    from wallflow.ui.main_window import MainWindow

    theme.apply(app)
    window = MainWindow(config, controller, player)
    window.show()
    for key in ("library", "sources", "playlists", "profiles", "schedule", "settings"):
        window.show_page(key)
        check(f"page '{key}' builds", True)
    window.show_page("library")
    check("library grid populated", window._library_view._model.rowCount() == len(library),
          str(window._library_view._model.rowCount()))

    from wallflow.ui.tray import app_icon

    check("icon renders", not app_icon(64).pixmap(64, 64).isNull())
    window.close()

    print("\n" + "=" * 52)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


def create_widget_for_kind(kind: WallpaperKind):
    from wallflow.live import widget_for_kind

    return widget_for_kind(kind)


if __name__ == "__main__":
    raise SystemExit(main())