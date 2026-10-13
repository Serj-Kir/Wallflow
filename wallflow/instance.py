"""Single-instance bookkeeping.

Two Wallflow processes would each create their own desktop-layer windows and
fight over the wallpaper, so the second one is stopped before it gets that far.
The pid file also lets ``--quit`` find the running instance.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

PID_FILENAME = "instance.pid"


def _pid_path() -> Path:
    return paths.data_dir() / PID_FILENAME


def _read_pid() -> int:
    try:
        return int(_pid_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def _alive(pid: int) -> bool:
    """True when the process exists and belongs to the current user."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True  # exists, just not ours to signal
        return False
    return True


def running_pid() -> int:
    """Pid of a live instance, or 0."""
    pid = _read_pid()
    if pid and pid != os.getpid() and _alive(pid):
        return pid
    return 0


def claim() -> int:
    """Record this process as the running instance.

    Returns the pid of another live instance, or 0 when this one now owns the
    lock.
    """
    other = running_pid()
    if other:
        return other
    try:
        _pid_path().write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write the instance file: %s", exc)
    return 0


def release() -> None:
    """Drop the lock, but only if this process still holds it."""
    if _read_pid() != os.getpid():
        return
    try:
        _pid_path().unlink(missing_ok=True)
    except OSError as exc:
        log.debug("Could not remove the instance file: %s", exc)


def terminate(timeout: float = 5.0) -> bool:
    """Ask a running instance to exit. Returns True if it was signalled."""
    pid = running_pid()
    if not pid:
        return False
    import signal
    import time

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        log.warning("Could not stop the running instance: %s", exc)
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return not _alive(pid)


__all__ = ["claim", "release", "running_pid", "terminate"]