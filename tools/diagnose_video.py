"""Diagnose video wallpaper playback: decode, frames, painting, window."""

import os
import sys
from pathlib import Path

os.environ.setdefault("WALLFLOW_NO_SANDBOX", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallflow.live.video import VideoWidget  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1]).expanduser().resolve()
    app = QApplication(sys.argv[:1])

    widget = VideoWidget(muted=True)
    events = {"error": [], "status": [], "frames": 0, "state": []}

    def on_error(err, msg=""):
        events["error"].append(f"{err}: {msg}")

    def on_status(status):
        events["status"].append(str(status).split(".")[-1])

    def on_state(state):
        events["state"].append(str(state).split(".")[-1])

    # Count frames with a second connection; disconnecting the first one would
    # take the widget's own receiver offline and hide the real behaviour.
    frames = {"n": 0}

    def counting(frame):
        if frame.isValid():
            frames["n"] += 1

    sink = widget._sink
    sink.videoFrameChanged.connect(counting)

    widget.error.connect(on_error)
    widget._player.mediaStatusChanged.connect(on_status)
    widget._player.playbackStateChanged.connect(on_state)
    widget.set_source(path)

    widget.show()
    widget.resize(640, 360)

    loop = QEventLoop()
    QTimer.singleShot(6000, loop.quit)
    loop.exec()

    pixmap = widget._receiver.pixmap()
    print(f"файл            : {path.name}  ({path.stat().st_size / 1e6:.1f} МБ)")
    print(f"длительность    : {widget.duration_ms()} мс")
    print(f"позиция         : {widget.position_ms()} мс")
    print(f"состояние       : {events['state'][-1] if events['state'] else '?'}")
    print(f"медиа-статусы   : {events['status']}")
    print(f"кадров декодировано: {frames['n']}")
    print(f"последний кадр  : {pixmap.width()}x{pixmap.height()}  null={pixmap.isNull()}")
    print(f"ошибки          : {events['error']}")

    ok = frames["n"] > 0 and not pixmap.isNull()
    print("ВЕРДИКТ:", "видео воспроизводится" if ok else "видео НЕ воспроизводится")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())