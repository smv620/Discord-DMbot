"""When a campaign's linked D&D Beyond sheets are read again while a session runs (#1128;
docs/PLAN.md, "Re-reading D&D Beyond"). Pure scheduling with an injected clock: no Discord, no
network, no database, so it is tested without them.

- **A change heard** at the table (see `sheet_changes`) re-reads that character's sheet once,
  about a minute later. Changes heard in that minute share the read.
- **Every 15 minutes**, out of combat, each linked sheet is read again, spread across the
  quarter hour so they don't all go at once. In combat only the change-heard reads run.
- **Limits:** at most one read per sheet per minute; after a refusal or an error from D&D
  Beyond a sheet waits, doubling each time up to 30 minutes, and nobody is told.

One `SheetWatch` per running session (so per campaign): nothing is shared between campaigns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dmbot.memory.sheet_changes import Heard

CHANGE_DELAY_S = 60.0  # a change heard is read about this long later
PERIOD_S = 15 * 60.0  # every sheet, out of combat
MIN_GAP_S = 60.0  # at most one read per sheet per minute
BACKOFF_START_S = 120.0
BACKOFF_MAX_S = 30 * 60.0
HEARD_MAX = 20  # changes kept per sheet waiting for a read; more is a flood, not a story


@dataclass(slots=True)
class _Sheet:
    next_periodic: float
    pending: list[Heard] = field(default_factory=list)
    due_at: float | None = None  # when the changes waiting are to be read
    last_read: float = -1e9
    blocked_until: float = 0.0
    backoff: float = 0.0


class SheetWatch:
    def __init__(self) -> None:
        self._sheets: dict[str, _Sheet] = {}

    def track(self, entity_ids: list[str], now: float) -> None:
        """The linked sheets there are now. New ones get a first periodic read spread evenly
        over the next quarter hour; ones that are gone are forgotten."""
        wanted = sorted(entity_ids)
        for entity_id in [e for e in self._sheets if e not in wanted]:
            del self._sheets[entity_id]
        fresh = [e for e in wanted if e not in self._sheets]
        for i, entity_id in enumerate(fresh):
            self._sheets[entity_id] = _Sheet(now + PERIOD_S * (i + 1) / (len(fresh) + 1))

    def note(self, entity_id: str, heard: list[Heard], now: float) -> bool:
        """Something was said about this character. False if the sheet isn't tracked."""
        sheet = self._sheets.get(entity_id)
        if sheet is None or not heard:
            return False
        if sheet.due_at is None:
            sheet.due_at = now + CHANGE_DELAY_S
        room = HEARD_MAX - len(sheet.pending)
        sheet.pending.extend(heard[: max(room, 0)])
        return True

    def due(self, now: float, *, in_combat: bool) -> list[str]:
        """The sheets to read now, oldest wish first. A sheet read, or refused, a moment ago
        waits (its changes are kept)."""
        out: list[tuple[float, str]] = []
        for entity_id, sheet in self._sheets.items():
            if now < sheet.blocked_until or now - sheet.last_read < MIN_GAP_S:
                continue
            if sheet.due_at is not None and now >= sheet.due_at:
                out.append((sheet.due_at, entity_id))
            elif not in_combat and now >= sheet.next_periodic:
                out.append((sheet.next_periodic, entity_id))
        return [entity_id for _, entity_id in sorted(out)]

    def take(self, entity_id: str, now: float) -> list[Heard]:
        """A read is starting: the changes it covers, and the time counts as a read."""
        sheet = self._sheets.get(entity_id)
        if sheet is None:
            return []
        heard, sheet.pending, sheet.due_at = sheet.pending, [], None
        sheet.last_read = now
        return heard

    def read_ok(self, entity_id: str, now: float) -> None:
        sheet = self._sheets.get(entity_id)
        if sheet is not None:
            sheet.backoff = 0.0
            sheet.blocked_until = 0.0
            sheet.next_periodic = now + PERIOD_S

    def read_failed(self, entity_id: str, now: float, heard: list[Heard]) -> None:
        """D&D Beyond refused or errored: wait twice as long as last time (at most 30
        minutes). The changes that were to be checked go back, to be read after the wait."""
        sheet = self._sheets.get(entity_id)
        if sheet is None:
            return
        sheet.backoff = min(BACKOFF_MAX_S, sheet.backoff * 2 if sheet.backoff else BACKOFF_START_S)
        sheet.blocked_until = now + sheet.backoff
        sheet.next_periodic = max(sheet.next_periodic, sheet.blocked_until)
        if heard:
            room = HEARD_MAX - len(sheet.pending)
            sheet.pending = [*heard[: max(room, 0)], *sheet.pending][:HEARD_MAX]
            sheet.due_at = sheet.blocked_until

    def waiting(self, entity_id: str) -> int:
        sheet = self._sheets.get(entity_id)
        return 0 if sheet is None else len(sheet.pending)
