"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from pathlib import Path

from . import paths, utils
from .version import APP_NAME, APP_VERSION


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="wallflow",
        description=f"{APP_NAME} — бесплатный движок живых обоев для Linux и Windows",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--minimized", action="store_true", help="запустить свёрнутым в трей")
    parser.add_argument("--hidden", action="store_true", help="запустить скрытно")
    parser.add_argument("--data-dir", help="использовать свою папку с данными")
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
        _signal_running_instance()
        return 0

    paths.ensure_dirs()

    # Qt Web Engine must be told about the sandbox before QApplication exists,
    # otherwise Chromium refuses to start in some containers.
    if "--no-sandbox" in sys.argv or os.environ.get("WALLFLOW_NO_SANDBOX"):
        os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu-driver-bug-workarounds")

    from PySide6.QtCore import QCoreApplication, Qt
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


def _install_signal_handling(app, window) -> None:
    """Make Ctrl+C work even though Qt owns the main loop."""
    try:
        signal.signal(signal.SIGINT, lambda *_: app.quit())
    except (ValueError, OSError):
        pass


def _signal_running_instance() -> None:
    """Ask an already-running Wallflow to quit (used by ``--quit``)."""
    marker = paths.data_dir() / "instance.pid"
    if not marker.exists():
        print("Wallflow не запущен")
        return
    try:
        pid = int(marker.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
        print("Закрытие Wallflow…")
    except (OSError, ValueError) as exc:
        print(f"Не удалось закрыть Wallflow: {exc}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())