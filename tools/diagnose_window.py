"""Reproduce the real wallpaper window path for a video and inspect the widget."""

import os
import sys
from pathlib import Path

os.environ.setdefault("WALLFLOW_NO_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallflow.config import Config  # noqa: E402
from wallflow.engine import create_stack  # noqa: E402
from wallflow.models import Wallpaper, WallpaperKind  # noqa: E402
from wallflow.player import WallpaperPlayer  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]).expanduser().resolve()
    app = QApplication(sys.argv[:1])

    wallpaper = Wallpaper.from_path(path)
    wallpaper.kind = WallpaperKind.from_path(path).value
    print(f"путь существует : {path.exists()}")
    print(f"тип             : {wallpaper.wallpaper_kind.value}")

    config = Config()
    player = WallpaperPlayer(create_stack(), config)
    player.refresh_monitors()
    print(f"мониторов       : {len(player.monitors())}")

    errors = []
    player.error.connect(errors.append)
    player.playback_error.connect(errors.append)
    player.set_current(wallpaper)

    loop = QEventLoop()
    QTimer.singleShot(6000, loop.quit)
    loop.exec()

    for slot in player._slots:
        content = slot.window.content
        print(f"слот {slot.id}: окно={slot.window.isVisible()} контент={type(content).__name__}")
        if content is None:
            continue
        print(f"   _source      : {content._source}")
        print(f"   size         : {content.size().width()}x{content.size().height()}")
        print(f"   visible      : {content.isVisible()}")
        pixmap = getattr(getattr(content, "_receiver", None), "pixmap", lambda: None)()
        print(f"   pixmap       : {pixmap.width() if pixmap else None}x"
              f"{pixmap.height() if pixmap else None}")
        player_state = getattr(getattr(content, "_player", None), "playbackState", None)
        if player_state is not None:
            print(f"   playback     : {str(player_state()).split('.')[-1]}")
            print(f"   position     : {content.position_ms()} мс")
    print(f"ошибки          : {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())