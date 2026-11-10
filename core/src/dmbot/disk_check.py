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

import logging
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
        alert: Callable[[Problem], None] = raise_alert,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._places = dict(places)
        self._warn, self._alert_at = warn_pct, alert_pct
        self._usage = usage
        self._alert = alert
        self._clock = clock
        self._said: dict[tuple[str, str], float] = {}  # (place, level) -> when it was last said

    def _due(self, place: str, level: str) -> bool:
        now = self._clock()
        last = self._said.get((place, level))
        if last is not None and now - last < SAY_EVERY_S:
            return False
        self._said[(place, level)] = now
        return True

    def check(self) -> list[Reading]:
        """One look at every place. A place that cannot be read is one WARNING line and the rest
        are still looked at. Returns what was read (for the tests and the log's sake)."""
        found: list[Reading] = []
        for name, path in self._places.items():
            try:
                total, used, free = self._usage(path)
            except OSError as exc:  # a path that is not there or not readable
                if self._due(name, "unreadable"):
                    log.warning(
                        "Disk check: couldn't read the %s disk (%s)", name, type(exc).__name__
                    )
                continue
            if total <= 0:
                continue
            reading = Reading(name, round(used * 100 / total), round(free / GB, 1))
            found.append(reading)
            level = level_of(used * 100 / total, self._warn, self._alert_at)
            if level == ALERT and self._due(name, ALERT):
                log.error(
                    "Disk check: the %s disk is %d%% full, %.1f GB free",
                    name, reading.used_pct, reading.free_gb,
                )  # fmt: skip
                self._alert(Problem.DISK_FULL)
            elif level == WARN and self._due(name, WARN):
                log.warning(
                    "Disk check: the %s disk is %d%% full, %.1f GB free",
                    name, reading.used_pct, reading.free_gb,
                )  # fmt: skip
        return found


def places_for(data_dir: Path) -> dict[str, Path]:
    """The places core can read: its data volume and the temp directory."""
    return {"data": data_dir, "temp": Path(tempfile.gettempdir())}
