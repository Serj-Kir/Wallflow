"""Verify a video loops instead of stopping on the last frame.

Generates a short clip with a visible frame counter, plays it past its
duration, and checks the position wrapped back to the start.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("WALLFLOW_NO_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallflow.live.video import LOOPS_FOREVER, LOOPS_ONCE, VideoWidget  # noqa: E402


def main() -> int:
    video = Path(sys.argv[1]).expanduser().resolve()
    app = QApplication(sys.argv[:1])

    probe = VideoWidget(muted=True, loops=LOOPS_FOREVER)
    probe.set_source(video)
    wait = QEventLoop()
    QTimer.singleShot(1500, wait.quit)
    wait.exec()
    duration = probe.duration_ms()
    probe.stop()

    if duration <= 0:
        print("не удалось определить длительность")
        return 1

    print(f"файл         : {video.name}")
    print(f"длительность : {duration / 1000:.1f} с")
    print("проверка     : перематываем к концу и смотрим, вернётся ли начало\n")

    failures = []

    def trial(label: str, loops: int, expect_loop: bool) -> None:
        widget = VideoWidget(muted=True, loops=loops)
        widget.set_source(video)

        ready = QEventLoop()
        QTimer.singleShot(1200, ready.quit)
        ready.exec()

        # Jump close to the end so the rollover happens within a second.
        widget.seek(max(0, duration - 900))

        trace: list[int] = []
        timer = QTimer()
        timer.setInterval(150)
        timer.timeout.connect(lambda: trace.append(widget.position_ms()))
        timer.start()

        watch = QEventLoop()
        QTimer.singleShot(4000, watch.quit)
        watch.exec()
        timer.stop()

        wrapped = any(b < a for a, b in zip(trace, trace[1:]) if a > 200)
        final = widget.position_ms()
        print(label)
        print(f"  loops         : {widget.loops}")
        print(f"  конечная позиция: {final} мс (длительность {duration} мс)")
        print(f"  трек          : {trace}")
        print(f"  позиция уменьшилась: {'да' if wrapped else 'нет'}")
        print(f"  состояние     : {str(widget.playback_state()).split('.')[-1]}")

        if expect_loop:
            if not wrapped:
                failures.append(f"{label}: позиция не вернулась к началу")
            if final >= duration - 100:
                failures.append(f"{label}: остановился на последнем кадре ({final} мс)")
        widget.stop()

    trial("LOOPS_FOREVER (ожидаем повтор)", LOOPS_FOREVER, expect_loop=True)
    print()
    trial("LOOPS_ONCE (ожидаем остановку)", LOOPS_ONCE, expect_loop=False)

    print()
    if failures:
        for failure in failures:
            print(f"ПРОВАЛ: {failure}")
        return 1
    print("ВЕРДИКТ: видео зациклено и не останавливается")
    return 0


if __name__ == "__main__":
    sys.exit(main())