"""The first live heads-up (docs/STORY_MEMORY.md, section 4, level 1; #2068): while a session runs,
a character the DM confirmed as dead is voiced at the table. Code only: no AI, no database, no
Discord. The bot reads the campaign's entry, its current condition and whether the campaign has
reached critical mass, and asks `heads_up` whether to say anything.

DMbot only points. It writes nothing until the DM presses a button (`ui/live_heads_up.py`), and a
heads-up goes to the DM screen alone, never to a transcript or a players' channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dmbot.memory.models import CONFIRMED, DEAD, Entity, State

COOLDOWN_S = 300.0  # the default gap between two live heads-ups (DMBOT_LIVE_HEADS_UP_MINUTES)
LIKELY = "likely"  # a direct clash with a confirmed fact is "likely" by rule (section 4)


@dataclass(slots=True)
class LiveSession:
    """What one session remembers about its live heads-ups: kept in memory for the session,
    never stored (a restart starts it fresh)."""

    ready: bool | None = None  # critical mass, read once per session; None: not read yet
    last_at: float | None = None  # when the last heads-up was claimed (monotonic seconds)
    quiet: set[str] = field(default_factory=set)  # entries the DM silenced for this session


@dataclass(frozen=True, slots=True)
class HeadsUp:
    entity_id: str
    name: str  # the name the table knows
    since: int | None  # when the entry died, as the session it happened in (its start time)
    level: str = LIKELY


def is_confirmed_character(entity: Entity | None) -> bool:
    return (
        entity is not None
        and entity.type == "character"
        and entity.status == CONFIRMED
        and entity.merged_into is None
    )


def heads_up(
    entity: Entity | None,
    state: State | None,
    *,
    ready: bool,
    session: LiveSession,
    now: float,
    cooldown_s: float = COOLDOWN_S,
) -> HeadsUp | None:
    """A heads-up for a line voiced as `entity`, or None. Only for a confirmed character whose
    current condition is dead, in a campaign at critical mass, outside the cooldown and not
    silenced for this session. A heads-up that is returned has used the cooldown (claimed at
    once, so two lines voiced together make one)."""
    if not ready or not is_confirmed_character(entity) or entity is None:
        return None
    if state is None or state.condition != DEAD or entity.id in session.quiet:
        return None
    if session.last_at is not None and now - session.last_at < cooldown_s:
        return None
    session.last_at = now
    return HeadsUp(entity.id, entity.name, state.from_session_at)


def keep_quiet(session: LiveSession, entity_id: str) -> None:
    """The DM said "not really them" or "ignore": no more about this entry this session."""
    session.quiet.add(entity_id)
