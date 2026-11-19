"""The party record and "the party" as one function (#4537, party knowledge slice 2a;
`docs/design/party_knowledge.md` §3 and rule 13.1; Supervisor's ruling on #4537, 2026-11-18).

"The party" is asked here and nowhere else: `current_members` (who is in it now) and `members_at`
(who was in it at a moment, such as a session's start). No reader lists player characters by role
to mean the party (`test_party_readers.py` lists the ones still to move, and only shrinks). A role
still says THAT an entry is a player character (sheets, "played by"); it is not the party. (The
"The party" faction entry and its `member_of` links are `memory/party.py`'s, #3036 and #3954.)

The record is `memory_party_spans` (0549): one row is one stretch of one player character in the
party. Rules kept here:
- **The party is a list of characters,** never a knower (§3).
- **A character with no stretch at all counts as in the party,** from always: every confirmed
  player character got one in 0549's backfill and every new one gets one when it is made, so this
  only keeps a character a route forgot to record from silently dropping out of the party. Slice
  2a changes no answer: the current party is today's list.
- **Only the DM opens or closes a stretch,** through the change log, so each is one ↩ Undo (rule
  13.3). Closing writes nothing else: every row the character holds stays (§6).
- **Stretches never overlap;** a character has at most one open one (0549's unique index), checked
  again here under the write's lock. A stretch opened by itself (a character made a player
  character) starts no earlier than the last one ended, so it can never fail that check; a time
  the DM picks is refused instead.
- **A character made an NPC keeps its open stretch** (it is simply not counted, being no player
  character now); made a player character again, it is in the party since the old date.
- **"A session running"** is one with no end time. A session the bot never closed (a stop
  mid-session) counts as running until it is closed or resumed and ended; its start then dates a
  stretch opened meanwhile, which is the safe side (in the party for that session).
Ids and times only, never a word of the story.
"""

from __future__ import annotations

import secrets
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from dmbot.memory._changes import ENTITIES, PARTY_SPANS, Changes, Scope
from dmbot.memory.models import CONFIRMED, PLAYER_CHARACTER

ENDED = ("left", "retired", "died", "away")  # why a stretch ended (`memory_party_spans.ended`)


class PartyError(ValueError):
    """A change the party record refuses (an overlap, a character not in the party)."""


@dataclass(frozen=True, slots=True)
class Span:
    """One stretch of one player character in the party."""

    id: str
    entity_id: str
    from_at: int
    to_at: int | None
    ended: str | None
    source: str

    @property
    def open(self) -> bool:
        return self.to_at is None

    def covers(self, at: int) -> bool:
        """In the party at `at` (from its start, up to but not at its end)."""
        return self.from_at <= at and (self.to_at is None or at < self.to_at)


def _span(row: dict[str, Any]) -> Span:
    return Span(
        id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        from_at=int(row["from_at"]),
        to_at=None if row["to_at"] is None else int(row["to_at"]),
        ended=None if row["ended"] is None else str(row["ended"]),
        source=str(row["source"]),
    )


async def player_characters(scope: Scope) -> list[str]:
    """Every confirmed player character's entry id, oldest first (not the party: a character who
    left is one too)."""
    rows = await scope.select(
        ENTITIES,
        " AND type = 'character' AND role = %s AND status = %s ORDER BY created_at, id",
        [PLAYER_CHARACTER, CONFIRMED],
    )
    return [str(r["id"]) for r in rows]


async def spans(scope: Scope, entity_ids: Collection[str] | None = None) -> list[Span]:
    """The stretches of these characters (all of the campaign's with None), oldest first."""
    if entity_ids is None:
        rows = await scope.select(PARTY_SPANS, " ORDER BY from_at, id")
    else:
        rows = await scope.select(
            PARTY_SPANS, " AND entity_id = ANY(%s) ORDER BY from_at, id", [list(entity_ids)]
        )
    return [_span(r) for r in rows]


async def _by_char(scope: Scope, pcs: list[str]) -> dict[str, list[Span]]:
    out: dict[str, list[Span]] = {}
    for s in await spans(scope, pcs):
        out.setdefault(s.entity_id, []).append(s)
    return out


async def current_members(scope: Scope) -> list[str]:
    """THE current party: every confirmed player character with an open stretch, or with none
    at all (see the module's note), oldest first."""
    pcs = await player_characters(scope)
    if not pcs:
        return []
    held = await _by_char(scope, pcs)
    return [c for c in pcs if c not in held or any(s.open for s in held[c])]


async def members_at(scope: Scope, at: int) -> list[str]:
    """The party at `at` (a session's start, for "the party as it was at that session", §2.5):
    every confirmed player character with a stretch covering it, or with none at all."""
    pcs = await player_characters(scope)
    if not pcs:
        return []
    held = await _by_char(scope, pcs)
    return [c for c in pcs if c not in held or any(s.covers(at) for s in held[c])]


async def table_members(scope: Scope) -> list[str] | None:
    """Who a player's answer at the table is for now (#4537 slice 3b, §8): the party at the
    running session's start when it differs from today's party (someone joined or left during the
    session, dated from its start); None when it is today's party (no session running, or the same
    members), so the caller keeps its usual path. One read of the stretches."""
    running = await running_session_start(scope)
    if running is None:
        return None
    pcs = await player_characters(scope)
    held = await _by_char(scope, pcs) if pcs else {}
    then = [c for c in pcs if c not in held or any(s.covers(running) for s in held[c])]
    now = [c for c in pcs if c not in held or any(s.open for s in held[c])]
    return None if set(then) == set(now) else then


async def running_session_start(scope: Scope) -> int | None:
    """The start of the campaign's session running now, if one is: a stretch that opens or closes
    during play counts from that session's start, so the session is the party's whole."""
    cur = await scope.conn.execute(
        "SELECT max(started_at) AS at FROM transcript_sessions WHERE guild_id = %s"
        " AND campaign_id = %s AND ended_at IS NULL",
        scope.ids,
    )
    row = await cur.fetchone()
    return None if row is None or row["at"] is None else int(row["at"])


async def first_session_start(scope: Scope) -> int | None:
    """When the campaign's first session began ("From the first session"), if it has one: a
    session with lines, as the session list counts them (an empty test join is no session)."""
    cur = await scope.conn.execute(
        "SELECT min(started_at) AS at FROM transcript_sessions WHERE guild_id = %s"
        " AND campaign_id = %s AND line_count > 0",
        scope.ids,
    )
    row = await cur.fetchone()
    return None if row is None or row["at"] is None else int(row["at"])


async def move_start(w: Changes, entity_id: str, from_at: int) -> Span:
    """Date the character's open stretch from `from_at` instead ("Since when has Mira been in the
    party?": the first session or a picked one, #4537 slice 2b). Logged, so one Undo puts it back.
    Refused for a character not in the party, and when it would overlap an earlier stretch."""
    held = await spans(w, [entity_id])
    open_ = [s for s in held if s.open]
    if not open_:
        raise PartyError("That character isn't in the party now.")
    if any(s.to_at is not None and s.to_at > from_at for s in held):
        raise PartyError("That would overlap a time already recorded.")
    row = await w.update(PARTY_SPANS, open_[0].id, {"from_at": from_at})
    return _span(row)


async def open_span(
    w: Changes, entity_id: str, *, from_at: int | None = None, source: str = "dm"
) -> Span | None:
    """Open a stretch for a character who has none open (made a player character, §3): from
    `from_at`, or by default from the running session's start, else now. Logged in the caller's
    change. None when one is open already (nothing to do). A default start is moved to the end of
    an earlier stretch if it falls inside it (never an error in an ordinary write); a `from_at`
    the DM picked that would overlap is refused. `source` is always the DM's: making a player
    character is the DM's word (a player's own pending character is confirmed by a DM)."""
    held = await spans(w, [entity_id])
    if any(s.open for s in held):
        return None
    last_end = max((s.to_at for s in held if s.to_at is not None), default=None)
    if from_at is None:
        start = await running_session_start(w) or w.now
        if last_end is not None:
            start = max(start, last_end)
    else:
        start = from_at
        if last_end is not None and start < last_end:
            raise PartyError("That would overlap a time already recorded.")
    row = await w.insert(
        PARTY_SPANS,
        {
            "id": secrets.token_hex(16), "entity_id": entity_id, "from_at": start, "to_at": None,
            "from_minute": None, "to_minute": None, "ended": None, "source": source,
            "created_at": w.now,
        },
    )  # fmt: skip
    return _span(row)


async def close_span(w: Changes, entity_id: str, ended: str, *, at: int | None = None) -> Span:
    """Close the character's open stretch ("Has left the party", §6): at `at`, or by default the
    running session's start (they weren't there for it), else now. Writes nothing else: every row
    the character holds stays. Refused for a character not in the party."""
    if ended not in ENDED:
        raise PartyError("That isn't one of the choices.")
    held = await spans(w, [entity_id])
    if not held:  # (never recorded, yet counted in the party: in it from always, now leaving)
        await open_span(w, entity_id, from_at=0, source="start")
        held = await spans(w, [entity_id])
    open_ = [s for s in held if s.open]
    if not open_:
        raise PartyError("That character isn't in the party now.")
    span = open_[0]
    end = at if at is not None else (await running_session_start(w) or w.now)
    end = max(end, span.from_at)  # (never before it began)
    row = await w.update(PARTY_SPANS, span.id, {"to_at": end, "ended": ended})
    return _span(row)


__all__ = [
    "ENDED",
    "PartyError",
    "Span",
    "close_span",
    "current_members",
    "first_session_start",
    "members_at",
    "move_start",
    "open_span",
    "player_characters",
    "running_session_start",
    "spans",
    "table_members",
]
