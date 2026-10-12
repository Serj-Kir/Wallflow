"""Application logic: profiles, playlists, schedules and pause policies.

This is the layer the UI talks to. It owns the :class:`WallpaperPlayer` and
decides *what* should be on screen and *whether* it is allowed to animate.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .engine.system import PowerState, SystemWatcher
from .models import (
    MonitorInfo,
    PauseReason,
    Playlist,
    Profile,
    ScheduleRule,
    Wallpaper,
    WallpaperKind,
)

log = logging.getLogger(__name__)

#: How often the policy timer re-evaluates pause conditions and rules.
POLICY_INTERVAL_MS = 2000
#: Rule boundaries (minutes since midnight) that the schedule timer watches.
RULE_TICK_MINUTES = 1


class PlaybackController(QObject):
    """High-level "what is on screen right now" controller."""

    profile_changed = Signal(object)      # Profile
    wallpaper_changed = Signal(object)    # Wallpaper | None
    pause_changed = Signal(bool, object)  # paused, PauseReason | None
    playlist_changed = Signal()
    status_message = Signal(str)

    def __init__(self, config, library, player, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._library = library
        self._player = player

        self._profiles: list[Profile] = config.load_profiles()
        self._playlists: list[Playlist] = config.load_playlists()
        self._rules: list[ScheduleRule] = config.load_rules()

        self._active_profile: Optional[Profile] = None
        self._active_playlist: Optional[Playlist] = None
        self._playlist_position = -1
        self._random = random.Random()

        self._manual_pause = False
        self._active_pause_reason: Optional[PauseReason] = None
        self._pending_advance_at = 0.0
        self._last_rule_id: str = ""
        self._restored_once = False

        self._system = SystemWatcher(interval=1.0)
        self._policy_timer = QTimer(self)
        self._policy_timer.setInterval(POLICY_INTERVAL_MS)
        self._policy_timer.timeout.connect(self._evaluate_policy)

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(RULE_TICK_MINUTES * 60 * 1000)
        self._clock_timer.timeout.connect(self._evaluate_rules)

        self._player.wallpaper_changed.connect(self.wallpaper_changed.emit)
        self._player.error.connect(self.status_message.emit)

    # ------------------------------------------------------------- bootstrap
    def start(self) -> None:
        """Begin playback, restoring the last profile unless told otherwise."""
        if not self._profiles:
            self._profiles = [Profile(name="По умолчанию")]
        self._policy_timer.start()
        self._clock_timer.start()
        self._evaluate_rules(force=True)

        wanted = self._config.get("startup_profile_id") or self._config.get("last_profile_id")
        profile = self.find_profile(wanted) if wanted else None
        if profile is None:
            profile = self._profiles[0]
        self.activate_profile(profile.id, remember=False)

    def stop(self) -> None:
        self._policy_timer.stop()
        self._clock_timer.stop()

    # -------------------------------------------------------------- profiles
    @property
    def profiles(self) -> list[Profile]:
        return list(self._profiles)

    @property
    def active_profile(self) -> Optional[Profile]:
        return self._active_profile

    def find_profile(self, profile_id: str) -> Optional[Profile]:
        for profile in self._profiles:
            if profile.id == profile_id:
                return profile
        return None

    def add_profile(self, name: str, *, wallpaper_id: Optional[str] = None) -> Profile:
        profile = Profile(name=name, wallpaper_id=wallpaper_id)
        self._profiles.append(profile)
        self.save_profiles()
        return profile

    def remove_profile(self, profile_id: str) -> bool:
        if len(self._profiles) <= 1:
            return False
        target = self.find_profile(profile_id)
        if target is None or target.locked:
            return False
        self._profiles = [p for p in self._profiles if p.id != profile_id]
        self.save_profiles()
        if self._active_profile and self._active_profile.id == profile_id:
            self.activate_profile(self._profiles[0].id, remember=False)
        return True

    def save_profiles(self) -> None:
        self._config.save_profiles(self._profiles)

    def activate_profile(
        self, profile_id: str, *, remember: bool = True, announce: bool = True
    ) -> bool:
        profile = self.find_profile(profile_id)
        if profile is None:
            return False
        self._active_profile = profile
        self._active_playlist = self.find_playlist(profile.playlist_id) if profile.playlist_id else None
        self._playlist_position = -1

        if remember:
            self._config.set("last_profile_id", profile.id)

        self._apply_now(announce=announce)
        self.profile_changed.emit(profile)
        if announce:
            self.status_message.emit(f"Профиль: {profile.name}")
        return True

    def _apply_now(self, *, announce: bool = False) -> None:
        profile = self._active_profile
        if profile is None:
            return

        monitor_map: dict[str, Optional[Wallpaper]] = {}
        has_override = False
        for monitor_id, wallpaper_id in profile.monitor_map().items():
            if not wallpaper_id:
                continue
            wallpaper = self._library.get(wallpaper_id)
            if wallpaper is not None:
                monitor_map[monitor_id] = wallpaper
                has_override = True

        if self._active_playlist and len(self._active_playlist):
            if self._playlist_position < 0 or self._playlist_position >= len(self._active_playlist):
                self._playlist_position = self._pick_playlist_index()
            wallpaper = self._current_playlist_wallpaper()
            transition_ms = self._active_playlist.transition_ms
            self._player.set_current(
                wallpaper, monitor_map=monitor_map or None, transition_ms=transition_ms
            )
            self._reset_playlist_timer()
        else:
            wallpaper = self._library.get(profile.wallpaper_id) if profile.wallpaper_id else None
            self._player.set_current(wallpaper, monitor_map=monitor_map or None)

        self._apply_accent(profile, wallpaper)
        if self._active_pause_reason is not None:
            self._player.set_paused(True)

    def _apply_accent(self, profile: Profile, wallpaper: Optional[Wallpaper]) -> None:
        setting = self._config.get("accent_color", "auto")
        if setting == "none":
            return
        if profile.accent_color:
            self._player.apply_accent(profile.accent_color)
        elif wallpaper is not None:
            from .ui.color import dominant_color

            self._player.apply_accent(dominant_color(Path(wallpaper.path)))

    # ------------------------------------------------------------- playlists
    @property
    def playlists(self) -> list[Playlist]:
        return list(self._playlists)

    @property
    def active_playlist(self) -> Optional[Playlist]:
        return self._active_playlist

    def find_playlist(self, playlist_id: str) -> Optional[Playlist]:
        for playlist in self._playlists:
            if playlist.id == playlist_id:
                return playlist
        return None

    def add_playlist(self, name: str, items: Optional[list[str]] = None) -> Playlist:
        playlist = Playlist(name=name, items=items or [])
        self._playlists.append(playlist)
        self.save_playlists()
        return playlist

    def remove_playlist(self, playlist_id: str) -> bool:
        self._playlists = [p for p in self._playlists if p.id != playlist_id]
        self.save_playlists()
        for profile in self._profiles:
            if profile.playlist_id == playlist_id:
                profile.playlist_id = None
        self.save_profiles()
        return True

    def save_playlists(self) -> None:
        self._config.save_playlists(self._playlists)

    def _pick_playlist_index(self) -> int:
        if not self._active_playlist or not self._active_playlist.items:
            return -1
        if self._active_playlist.shuffle:
            return self._random.randrange(len(self._active_playlist.items))
        return 0

    def _current_playlist_wallpaper(self) -> Optional[Wallpaper]:
        if not self._active_playlist or self._playlist_position < 0:
            return None
        items = self._active_playlist.items
        if not items:
            return None
        index = self._playlist_position % len(items)
        return self._library.get(items[index])

    def next(self) -> None:
        self.step(1)

    def previous(self) -> None:
        self.step(-1)

    def step(self, delta: int) -> None:
        if not self._active_playlist or not self._active_playlist.items:
            return
        count = len(self._active_playlist.items)
        self._playlist_position = (self._playlist_position + delta) % count
        self._apply_now(announce=True)
        self.playlist_changed.emit()

    def jump_to(self, index: int) -> None:
        if not self._active_playlist:
            return
        items = self._active_playlist.items
        if 0 <= index < len(items):
            self._playlist_position = index
            self._apply_now(announce=True)
            self.playlist_changed.emit()

    def _reset_playlist_timer(self) -> None:
        self._pending_advance_at = 0.0
        playlist = self._active_playlist
        if playlist is None:
            return
        if playlist.interval_seconds > 0 and not playlist.paused:
            self._pending_advance_at = time.monotonic() + playlist.interval_seconds

    def set_playlist(self, playlist_id: Optional[str]) -> None:
        profile = self._active_profile
        if profile is None:
            return
        profile.playlist_id = playlist_id
        self.save_profiles()
        self._active_playlist = self.find_playlist(playlist_id) if playlist_id else None
        self._playlist_position = -1
        self._apply_now(announce=True)
        self.playlist_changed.emit()

    def set_wallpaper(self, wallpaper_id: Optional[str]) -> None:
        profile = self._active_profile
        if profile is None:
            return
        profile.wallpaper_id = wallpaper_id
        if wallpaper_id:
            profile.playlist_id = None
            self._active_playlist = None
        self.save_profiles()
        self._apply_now(announce=True)

    def set_monitor_wallpaper(self, monitor_id: str, wallpaper_id: Optional[str]) -> None:
        profile = self._active_profile
        if profile is None:
            return
        profile.set_monitor(monitor_id, wallpaper_id)
        self.save_profiles()
        self._apply_now()

    # ----------------------------------------------------------------- pause
    @property
    def paused(self) -> bool:
        return self._active_pause_reason is not None

    @property
    def pause_reason(self) -> Optional[PauseReason]:
        return self._active_pause_reason

    def set_manual_pause(self, paused: bool) -> None:
        if self._manual_pause == paused:
            return
        self._manual_pause = paused
        self._evaluate_policy(force=True)

    def toggle_pause(self) -> None:
        self.set_manual_pause(not self._manual_pause)

    def _evaluate_policy(self, *, force: bool = False) -> None:
        """Recompute whether playback should be paused, and why."""
        reason = self._resolve_pause_reason()
        if reason != self._active_pause_reason or force:
            previous = self._active_pause_reason
            self._active_pause_reason = reason
            self._player.set_paused(reason is not None)
            self.pause_changed.emit(reason is not None, reason)
            if reason is not None and previous is None:
                self.status_message.emit(self._describe_reason(reason))

        # Playlist ticking.
        if (
            self._pending_advance_at
            and time.monotonic() >= self._pending_advance_at
            and not self.paused
        ):
            self._pending_advance_at = 0.0
            self.step(1)

    def _resolve_pause_reason(self) -> Optional[PauseReason]:
        if self._manual_pause:
            return PauseReason.USER
        power, display = self._system.poll()
        if self._config.get("pause_on_fullscreen", True) and display.fullscreen:
            return PauseReason.FULLSCREEN
        if self._config.get("pause_on_lock", True) and display.locked:
            return PauseReason.LOCKED
        if self._config.get("pause_on_battery", False) and power.on_battery:
            threshold = int(self._config.get("battery_pause_threshold", 20))
            if threshold <= 0 or power.percent <= threshold:
                return PauseReason.BATTERY
        if self._config.get("pause_when_idle", False):
            idle_limit = float(self._config.get("idle_minutes", 5)) * 60
            if display.idle_seconds >= idle_limit:
                return PauseReason.SUSPEND
        if self._config.get("pause_on_remote_session", True) and _remote_session():
            return PauseReason.REMOTE
        return None

    @staticmethod
    def _describe_reason(reason: PauseReason) -> str:
        return {
            PauseReason.USER: "Воспроизведение приостановлено",
            PauseReason.FULLSCREEN: "Пауза: полноэкранное приложение",
            PauseReason.BATTERY: "Пауза: питание от батареи",
            PauseReason.LOCKED: "Пауза: экран заблокирован",
            PauseReason.REMOTE: "Пауза: удалённый сеанс",
            PauseReason.SUSPEND: "Пауза: система простаивает",
        }.get(reason, "Пауза")

    # ----------------------------------------------------------------- rules
    @property
    def rules(self) -> list[ScheduleRule]:
        return list(self._rules)

    def save_rules(self) -> None:
        self._config.save_rules(self._rules)

    def add_rule(self, rule: ScheduleRule) -> None:
        self._rules.append(rule)
        self.save_rules()

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.id != rule_id]
        self.save_rules()
        return len(self._rules) != before

    def _evaluate_rules(self, *, force: bool = False) -> None:
        now = datetime.now()
        minutes = now.hour * 60 + now.minute
        weekday = now.weekday()
        matching = sorted(
            (r for r in self._rules if r.matches(minutes, weekday)),
            key=lambda r: r.priority,
            reverse=True,
        )
        winner = matching[0] if matching else None
        winner_id = winner.id if winner else ""

        if winner_id == self._last_rule_id and not force:
            return
        self._last_rule_id = winner_id

        if winner is None:
            # Rules stopped matching: fall back to the startup profile.
            fallback_id = self._config.get("startup_profile_id")
            if fallback_id and self.find_profile(fallback_id) is not None:
                self.activate_profile(fallback_id, remember=False)
                self.status_message.emit("Расписание вернуло базовый профиль")
            return
        if self.find_profile(winner.profile_id) is None:
            return
        if self._active_profile and self._active_profile.id == winner.profile_id:
            return
        self.activate_profile(winner.profile_id, remember=False)
        self.status_message.emit(f"Расписание: {winner.name or 'правило'}")

    # --------------------------------------------------------------- helpers
    def resume_now(self) -> None:
        """Pick the next wallpaper immediately, ignoring the interval."""
        self._pending_advance_at = 0.0
        self.step(1)

    def library_item(self, wallpaper_id: str) -> Optional[Wallpaper]:
        return self._library.get(wallpaper_id)

    def monitors(self) -> list[MonitorInfo]:
        return self._player.monitors()


def _remote_session() -> bool:
    """Detect RDP/SSH sessions, where heavy rendering is a poor idea."""
    import os

    for env in ("SSH_CONNECTION", "SSH_TTY", "RDP_SESSION", "SESSIONNAME"):
        value = os.environ.get(env, "")
        if value and ("RDP" in value.upper() or env.startswith("SSH")):
            return True
    return False


__all__ = ["PlaybackController", "POLICY_INTERVAL_MS"]