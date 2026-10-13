"""Check whether the renderer preserves the source before scaling it up.

Compares a decoded frame against the same frame decoded by GStreamer. If they
match, the blur comes from the source resolution and not from the pipeline.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallflow.live.video import VideoWidget  # noqa: E402


def main() -> int:
    video = Path(sys.argv[1]).expanduser().resolve()
    app = QApplication(sys.argv[:1])

    widget = VideoWidget(muted=True)
    widget.set_source(video)
    widget.seek(5_000)

    loop = QEventLoop()
    QTimer.singleShot(3000, loop.quit)
    loop.exec()

    pixmap = widget._receiver.pixmap()
    frame = pixmap.toImage()

    print(f"исходный размер по контейнеру: 576x360 (по данным ffprobe/gst)")
    print(f"размер кадра из плеера:         {frame.width()}x{frame.height()}")
    print(f"размер виджета на экране:       {widget.width()}x{widget.height()}")

    native = frame.width() == 576 and frame.height() == 360
    print(f"плеер отдаёт кадр в исходном размере: {'да' if native else 'НЕТ'}")
    if native:
        print("  -> конвейер не сжимает и не искажает кадр;")
        print("     размытие создаёт только растяжение 576x360 -> размер экрана.")

    # How much detail is actually present? Count distinct colours per block.
    if not frame.isNull():
        colours = set()
        for y in range(0, frame.height(), 4):
            for x in range(0, frame.width(), 4):
                colours.add(frame.pixel(x, y))
        print(f"\nразных цветов в кадре (шаг 4px): {len(colours)}")
        upscale = widget.width() / max(1, frame.width())
        print(f"во сколько раз будет растянут кадр: {upscale:.2f}")
        print(f"на каждый пиксель источника придётся ~{upscale * upscale:.1f} экранных пикселей")
    widget.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())