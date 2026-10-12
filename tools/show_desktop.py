"""Close every normal window so the desktop (and its live wallpaper) is visible."""

import subprocess
import sys


def main() -> int:
    out = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True).stdout
    hidden = 0
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        wid, desktop = parts[0], parts[2]
        title = parts[4] if len(parts) > 4 else ""
        if "wallpaper layer" in title:
            continue  # never hide the wallpaper itself
        if desktop == "-1":
            continue  # already hidden
        r = subprocess.run(["wmctrl", "-i", "-r", wid, "-b", "add,hidden"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            hidden += 1
    print(f"скрыто окон: {hidden}")
    return 0


if __name__ == "__main__":
    sys.exit(main())