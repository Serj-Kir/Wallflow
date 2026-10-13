"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from . import paths, utils
from .models import Wallpaper, WallpaperKind
from .version import APP_NAME, APP_VERSION

#: Human-readable names used by the ``--set`` command.
KIND_LABEL = {
    WallpaperKind.IMAGE: "фото",
    WallpaperKind.VIDEO: "видео",
    WallpaperKind.WEB: "HTML",
    WallpaperKind.SCENE: "сцена",
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wallflow",
        description=f"{APP_NAME} — бесплатный движок живых обоев для Linux и Windows",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--minimized", action="store_true", help="запустить свёрнутым в трей")
    parser.add_argument("--hidden", action="store_true", help="запустить скрытно")
    parser.add_argument("--data-dir", help="использовать свою папку с данными")
    parser.add_argument(
        "--set",
        metavar="ФАЙЛ",
        help="применить обои из файла и выйти (папка — HTML-обои, .scene — сцена)",
    )
    parser.add_argument("--profile", metavar="ИМЯ", help="применить профиль по имени при запуске")
    parser.add_argument("--list-monitors", action="store_true", help="показать мониторы и выйти")
    parser.add_argument("--reset-settings", action="store_true", help="сбросить настройки")
    parser.add_argument("--verbose", "-v", action="store_true", help="подробный журнал")
    parser.add_argument("--quit", action="store_true", help="закрыть уже запущенный Wallflow")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.data_dir:
        os.environ["WALLFLOW_DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())

    utils.setup_logging("DEBUG" if args.verbose else "INFO")
    log = logging.getLogger("wallflow")

    if args.quit:
        from . import instance

        if instance.terminate():
            print("Закрытие Wallflow…")
            return 0
        print("Wallflow не запущен")
        return 1

    paths.ensure_dirs()

    # Two instances would each draw their own desktop window and fight over
    # the wallpaper, so only the first one is allowed to run.
    from . import instance as instance_guard

    other = instance_guard.claim()
    if other:
        print(
            f"Wallflow уже запущен (процесс {other}).\n"
            "Закройте его или выполните: python -m wallflow --quit",
            file=sys.stderr,
        )
        return 1

    # Qt Web Engine must be told about the sandbox before QApplication exists,
    # otherwise Chromium refuses to start in some containers.
    if "--no-sandbox" in sys.argv or os.environ.get("WALLFLOW_NO_SANDBOX"):
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-driver-bug-workarounds")

    from PySide6.QtCore import QCoreApplication, QTimer, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv[:1] + (argv if argv is not None else sys.argv[1:]))
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Wallflow")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive

    from . import autostart
    from .config import Config
    from .controller import PlaybackController
    from .engine import create_stack
    from .models import Wallpaper
    from .player import WallpaperPlayer
    from .ui import theme
    from .ui.main_window import MainWindow

    config = Config()
    if args.reset_settings:
        config.reset_defaults()
        log.info("Settings were reset")

    theme.apply(app, _accent(config))
    autostart.sync(bool(config.get("start_with_system")))

    backend = create_stack()
    log.info("Backend: %s", backend.primary.name)

    player = WallpaperPlayer(backend, config, parent=app)
    player.refresh_monitors()

    if args.list_monitors:
        _print_monitors(player, backend)
        return 0

    if args.set:
        return _apply_one(args.set, config, backend, player, app)

    # The library is shared by the player, the controller and the UI, so it is
    # built once here and handed to each of them.
    from .library import Library

    library = Library(config.get("library_dirs", []) or [])
    controller = PlaybackController(config, library, player, parent=app)

    window = MainWindow(config, controller, player)

    player.refresh_monitors()
    controller.start()

    if args.minimized or args.hidden:
        window.hide()
    else:
        window.show()

    _install_signal_handling(app, window)

    log.info("%s %s started", APP_NAME, APP_VERSION)
    return app.exec()


def _accent(config) -> str:
    from .ui import theme

    value = config.get("accent_color", "auto")
    return value if isinstance(value, str) and value.startswith("#") else theme.ACCENT


def _print_monitors(player, backend) -> None:
    print(f"Система: {backend.primary.label}  ({backend.primary.name})")
    capabilities = backend.capabilities
    print(f"  свои обои на каждый монитор: {'да' if capabilities.per_monitor else 'нет'}")
    print(f"  сессия: {'Wayland' if player._is_wayland() else 'X11'}")
    print("Мониторы:")
    for index, monitor in enumerate(player.monitors(), start=1):
        flags = ["основной"] if monitor.primary else []
        if monitor.scale != 1.0:
            flags.append(f"{monitor.scale:g}x")
        print(
            f"  {index}. {monitor.id}  {monitor.width}x{monitor.height}"
            f"  @({monitor.x},{monitor.y})  {' '.join(flags)}".rstrip()
        )
    if not player.monitors():
        print("  (не обнаружены)")


def _apply_one(path_str: str, config, backend, player, app) -> int:
    """Apply a single wallpaper without starting the full UI."""
    path = Path(path_str).expanduser().resolve()
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 2

    if path.is_dir():
        wallpaper = Wallpaper.from_path(path)
        wallpaper.kind = WallpaperKind.WEB.value
    else:
        wallpaper = Wallpaper.from_path(path)
        wallpaper.kind = WallpaperKind.from_path(path).value

    player.set_current(wallpaper)
    print(f"Применено: {wallpaper.name}  [{KIND_LABEL.get(wallpaper.wallpaper_kind, '')}]")
    visible = any(slot.window.isVisible() for slot in player._slots)
    print(f"Режим: {'своё окно' if visible else 'системный'}")

    from . import instance

    # --set has no main window but still needs a clean shutdown path.
    app.aboutToQuit.connect(instance.release)
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, lambda *_: app.quit())
        except (ValueError, OSError):
            pass

    # An animated wallpaper only stays on screen while the process lives.
    if wallpaper.is_animated:
        print("Анимация идёт, пока работает эта команда (Ctrl+C — остановить).")
        return app.exec()
    QTimer.singleShot(1500, app.quit)
    return app.exec()


def _install_signal_handling(app, window) -> None:
    """Let Ctrl+C and ``--quit`` shut the app down cleanly.

    Qt only handles SIGINT. Without a SIGTERM handler the process would die
    mid-shutdown, leaving the instance lock behind and skipping the config
    flush, so both are routed through ``app.quit()``.
    """
    from . import instance

    def request_quit(*_args) -> None:
        app.quit()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, request_quit)
        except (ValueError, OSError):
            pass  # not the main thread, or the signal is unavailable here

    app.aboutToQuit.connect(instance.release)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())