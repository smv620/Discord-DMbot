"""A warning before the server's disk is full (#4075).

On 2026-11-10 the Docker disk reached 100% from old test images and Postgres panicked on its
checkpoint writes; nothing had told anyone it was filling. Every few minutes this reads the free
space of the paths core can see: the data volume core shares (`DMBOT_DATA_DIR`, the `dmbot-data`
volume) and the temp directory (where documents and audio are unpacked). It uses
`shutil.disk_usage`, never a shell. The database's own volume is not mounted in core, so a
filesystem shared with these shows up here, and one that is not can only be seen from the host.

At the warning level (85% by default) a WARNING line is logged, at the alert level (95%) an ERROR
line and one fixed message to the owner by the existing owner alert (`alerts`, never to a DM, a
player or a channel). Each level speaks at most once an hour for a path. Only the path's name, a
percentage and the free space in GB ever appear: no file names, no sizes of anything else.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from dmbot.alerts import Problem, raise_alert

log = logging.getLogger(__name__)

CHECK_EVERY_S = 600  # every ten minutes
SAY_EVERY_S = 3600  # each level, each path, at most once an hour
GB = 1024**3

OK, WARN, ALERT = "ok", "warn", "alert"


@dataclass(frozen=True, slots=True)
class Reading:
    path: str  # the name of the place, as the log says it ("data", "temp")
    used_pct: int
    free_gb: float


@dataclass(frozen=True, slots=True)
class Result:
    """One look: what was read, and the owner alerts that are due. The alerts are sent by the
    caller, on the event loop (`raise_alert` starts a task, which a worker thread cannot)."""

    readings: list[Reading]
    alerts: list[Problem]


def _inodes(path: Path) -> tuple[int, int]:
    """(total, free) inodes of the filesystem `path` is on: a disk of tiny files can have free bytes
    and no free inodes, and then nothing can be written either (#4077)."""
    st = os.statvfs(path)
    return st.f_files, st.f_ffree


def level_of(used_pct: float, warn_pct: int, alert_pct: int) -> str:
    """Which level a percentage of the disk used is at. Pure."""
    if used_pct >= alert_pct:
        return ALERT
    if used_pct >= warn_pct:
        return WARN
    return OK


class DiskWatch:
    """Reads the places that matter and says so when one is filling."""

    def __init__(
        self,
        places: Mapping[str, Path],
        *,
        warn_pct: int = 85,
        alert_pct: int = 95,
        usage: Callable[[Path], tuple[int, int, int]] = shutil.disk_usage,
        inodes: Callable[[Path], tuple[int, int]] = _inodes,
        device: Callable[[Path], int] = lambda path: os.stat(path).st_dev,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._places = dict(places)
        self._warn, self._alert_at = warn_pct, alert_pct
        self._usage = usage
        self._inodes = inodes
        self._device = device
        self._clock = clock
        self._said: dict[tuple[str, str], float] = {}  # (place, level) -> when it was last said

    def _due(self, place: str, level: str) -> bool:
        now = self._clock()
        last = self._said.get((place, level))
        if last is not None and now - last < SAY_EVERY_S:
            return False
        self._said[(place, level)] = now
        return True

    def check(self) -> Result:
        """One look at every place (in a worker thread: it only reads and logs). A place that
        cannot be read is one WARNING line and the rest are still looked at. Places on one disk are
        one place: on a default Docker install the data volume and the temp directory share a
        filesystem, and saying it twice would only repeat itself."""
        found: list[Reading] = []
        alerts: list[Problem] = []
        seen: set[int] = set()
        for name, path in self._places.items():
            try:
                device = self._device(path)
                if device in seen:
                    continue  # (this disk was just looked at under another name)
                seen.add(device)
                total, used, free = self._usage(path)
            except OSError as exc:  # a path that is not there or not readable
                if self._due(name, "unreadable"):
                    log.warning(
                        "Disk check: couldn't read the %s disk (%s)", name, type(exc).__name__
                    )
                continue
            if total <= 0:
                continue
            # Rounded down, as the level is: 94.6% is 94% and a warning, never "95% full" at the
            # warning level. `df` counts what a normal user may use, so it can read a little higher.
            pct = math.floor(used * 100 / total)
            reading = Reading(name, pct, round(free / GB, 1))
            found.append(reading)
            level = level_of(pct, self._warn, self._alert_at)
            if level == ALERT and self._due(name, ALERT):
                log.error(
                    "Disk check: the %s disk is %d%% full, %.1f GB free",
                    name, reading.used_pct, reading.free_gb,
                )  # fmt: skip
                alerts.append(Problem.DISK_FULL)
            elif level == WARN and self._due(name, WARN):
                log.warning(
                    "Disk check: the %s disk is %d%% full, %.1f GB free",
                    name, reading.used_pct, reading.free_gb,
                )  # fmt: skip
            self._look_at_inodes(name, path, alerts)
        return Result(found, alerts)

    def _look_at_inodes(self, name: str, path: Path, alerts: list[Problem]) -> None:
        """The inodes of a place, at the same levels and once an hour, under their own keys
        so the bytes check and this one do not silence each other (#4077). One owner alert
        per look at most, even when both are full."""
        key = f"{name} inodes"
        try:
            total, free = self._inodes(path)
        except OSError as exc:
            if self._due(key, "unreadable"):
                log.warning(
                    "Disk check: couldn't read the %s disk's inodes (%s)",
                    name,
                    type(exc).__name__,
                )
            return
        if total <= 0:
            return
        pct = math.floor((total - free) * 100 / total)
        level = level_of(pct, self._warn, self._alert_at)
        if level == ALERT and self._due(key, ALERT):
            log.error("Disk check: the %s disk's inodes are %d%% used", name, pct)
            if Problem.DISK_FULL not in alerts:
                alerts.append(Problem.DISK_FULL)
        elif level == WARN and self._due(key, WARN):
            log.warning("Disk check: the %s disk's inodes are %d%% used", name, pct)


def places_for(data_dir: Path) -> dict[str, Path]:
    """The places core can read: its data volume and the temp directory."""
    return {"data": data_dir, "temp": Path(tempfile.gettempdir())}


async def look(watch: DiskWatch) -> Result:
    """One look from the event loop: the reading in a worker thread (a disk read), the owner alert
    from here (a task can only be started on the loop). Never raises: a failed look is one line."""
    try:
        result = await asyncio.to_thread(watch.check)
    except Exception as exc:  # the check must never stop the bot
        log.warning("Disk check failed (%s)", type(exc).__name__)
        return Result([], [])
    if Problem.DISK_FULL in result.alerts:
        try:  # (a named problem, as `alerts` requires of every caller: never a value that was read)
            raise_alert(Problem.DISK_FULL)
        except Exception as exc:  # an alert that cannot start is a line, not the end of the loop
            log.warning("Disk check: couldn't raise the owner alert (%s)", type(exc).__name__)
    return result
