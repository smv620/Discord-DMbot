"""The pre-session note (#1403, docs/STORY_MEMORY.md section 7): a few lines on the DM screen when
a session starts, from what the DM has confirmed: "Before tonight: last time, who was talked
about and what is open about them, what was lost or ended, what is still waiting to be checked".

It is DM-screen content and is built for the DM alone: secret names and facts are included
(recall for the `dm_screen` audience), and nothing here is written anywhere else or put in a
transcript. No AI is called. Pure wording is in `compose`; `PreSession` does the reads, a few
indexed lookups plus one recall, so it takes milliseconds on a campaign of thousands of entries.

The note is never empty padding: a campaign with nothing confirmed gets no note at all.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from dmbot.memory.claims import ClaimStore
from dmbot.memory.models import GOING
from dmbot.memory.recall import DM_SCREEN, FACT, RecallService
from dmbot.memory.store import MemoryStore
from dmbot.memory.threads import LIVE_STATES, PROMISE

log = logging.getLogger(__name__)


HEADER = "📜 **Before tonight**\n*From what you confirmed. Only you see this.*"
MAX_LINES = 8  # lines under the header
MAX_CHARS = 1000  # the whole note
LINE_CHARS = 140  # one line
SEEDS = 5  # the names most heard last session
FACT_LINES = 3
QUIET_PREFIX = "🧵 Quiet threads: "
QUIET_NAMES = 3  # thread names on the quiet-threads line, then "+N more"
DUE_PREFIX = "🤝 Due soon: "
MORE_DUE = "🤝 +"
DUE_LINES = 2  # promises the DM marked Due soon that get a line, then "+N more" (#1680)
CONDITION_LINES = 3
RECALL_BUDGET = 1500  # characters one recall may hold
# What a condition reads as. Only the ones worth remembering: "alive" and "intact" are the
# ordinary state of things, so they are not news.
CONDITION_WORDS = {
    "dead": "is dead",
    "lost": "is lost",
    "destroyed": "is destroyed",
    "missing": "is missing",
    "captured": "is captured",
}


class _LastRecap(Protocol):
    # Read-only properties: the recap store's `Job` is a frozen dataclass, whose fields a plain
    # protocol attribute (settable) would refuse.
    @property
    def title(self) -> str: ...

    @property
    def game_day(self) -> int | None: ...


class RecapBridge(Protocol):
    """What the note asks of the recap store: its latest *posted* recap before a time."""

    async def bridge(self, guild_id: int, campaign_id: str, before: int) -> _LastRecap | None: ...


@dataclass(frozen=True, slots=True)
class Note:
    text: str
    lines: tuple[str, ...]
    waiting: int  # claims waiting to be checked: the poster adds the button when this is above 0
    quiet: int = 0  # threads on the note's thread lines (quiet or due soon): the poster adds the
    # 🧵 Threads button when above 0


@dataclass(frozen=True, slots=True)
class Due:
    """A promise the DM marked Due soon, in the DM's own words (the due note is never parsed)."""

    what: str
    by: str = ""  # who promised, "" until the DM says
    to: str = ""  # promised to whom
    due: str = ""


@dataclass(frozen=True, slots=True)
class Facts:
    """What the note is made of, already selected (so the wording needs no database)."""

    recap_title: str | None = None
    recap_day: int | None = None
    open_facts: Sequence[str] = ()  # plain sentences, best first
    conditions: Sequence[tuple[str, str]] = ()  # (name, condition), newest first
    waiting: int = 0
    quiet_threads: Sequence[str] = ()  # names of the threads gone quiet, longest quiet first
    due_soon: Sequence[Due] = ()  # promises marked Due soon, longest quiet first


def _clip(text: str, limit: int = LINE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _plain(text: str) -> str:
    """A name or a title as plain text inside the note's own bold and italics: nothing in it can
    open or close markdown or make a link (`* _ ~ | ` > [ ] ( ) <` get a backslash)."""
    out = []
    for ch in " ".join(text.split()):
        out.append("\\" + ch if ch in "*_~|`>[]()<\\" else ch)
    return "".join(out)


def _due_line(d: Due) -> str:
    """ "🤝 Due soon: Kael → the village: bring the healer back (due: the full moon)". Until the
    DM says who promised and to whom, the short form: "🤝 Due soon: <what>"."""
    what = _plain(d.what)
    core = f"{_plain(d.by)} → {_plain(d.to)}: {what}" if d.by and d.to else what
    note = f" (due: {_plain(d.due)})" if d.due else ""
    return _clip(f"{DUE_PREFIX}{core}{note}")


def compose(facts: Facts) -> Note | None:
    """The note, or None when there is nothing to say. Newest first: the last recap, what is
    open about the names heard last time, what was lost or ended, then what waits to be checked.
    At most `MAX_LINES` lines and `MAX_CHARS` characters; a line that doesn't fit is left out
    (never cut mid-way)."""
    lines: list[str] = []
    if facts.recap_title:
        day = f" (story day {facts.recap_day})" if facts.recap_day else ""
        lines.append(_clip(f"Last recap: *{_plain(facts.recap_title)}*{day}"))
    for sentence in list(facts.open_facts)[:FACT_LINES]:
        lines.append(_clip(f"• {_plain(sentence)}"))
    for name, condition in list(facts.conditions)[:CONDITION_LINES]:
        words = CONDITION_WORDS.get(condition)
        if words:
            lines.append(_clip(f"• {_plain(name)} {words}"))
    if facts.waiting > 0:
        said = (
            "1 thing someone said is"
            if facts.waiting == 1
            else f"{facts.waiting} things people said are"
        )
        lines.append(f"🗣️ {said} waiting for your check")
    # The thread lines come last: when the note is full they are cut, never the claims line
    # that has a button of its own.
    if facts.quiet_threads:  # in-game content: it stays on the DM screen (#1675)
        shown = [_plain(n) for n in facts.quiet_threads[:QUIET_NAMES]]
        more = len(facts.quiet_threads) - len(shown)
        tail = f" (+{more} more)" if more > 0 else ""
        lines.append(_clip(f"{QUIET_PREFIX}{', '.join(shown)}{tail}"))
    due_lines = [_due_line(d) for d in facts.due_soon[:DUE_LINES]]
    if due_lines and len(facts.due_soon) > DUE_LINES:
        due_lines.append(f"🤝 +{len(facts.due_soon) - DUE_LINES} more promises due soon")
    lines.extend(due_lines)  # in-game content: the DM screen only (#1680)
    lines = lines[:MAX_LINES]
    if not lines:
        return None
    kept: list[str] = []
    size = len(HEADER)
    for line in lines:
        if size + 1 + len(line) > MAX_CHARS:
            continue
        kept.append(line)
        size += 1 + len(line)
    if not kept:
        return None
    # No button for a line that was cut.
    if not any(line.startswith(DUE_PREFIX) for line in kept):  # no orphaned "+N more"
        kept = [line for line in kept if not line.startswith(MORE_DUE)]
    shown_quiet = any(line.startswith(QUIET_PREFIX) for line in kept)
    shown_due = any(line.startswith(DUE_PREFIX) for line in kept)
    # A due-soon promise that has also gone quiet is on both lines: counted once.
    names = {n for n in facts.quiet_threads if shown_quiet}
    names |= {d.what for d in facts.due_soon if shown_due}
    threads = len(names)
    return Note(
        "\n".join([HEADER, *kept]),
        tuple(kept),
        max(0, facts.waiting),
        threads,
    )


class PreSession:
    def __init__(
        self,
        memory: MemoryStore,
        recall: RecallService,
        claims: ClaimStore,
        recaps: RecapBridge | None = None,
    ) -> None:
        self._memory = memory
        self._recall = recall
        self._claims = claims
        self._recaps = recaps

    async def note(
        self,
        guild_id: int,
        campaign_id: str,
        *,
        now: int,
        opted_in: Collection[int],
        stalled_after: int = 3,
    ) -> Note | None:
        """The note for one campaign, from its confirmed facts only (this campaign's: every read
        names the server and the campaign). None when nothing is confirmed. `opted_in` is who is
        recorded now: the waiting-questions count drops those citing anyone who is not (as the
        after-session count does)."""
        async with self._memory._read(guild_id, campaign_id) as scope:
            # The latest session that has a confirmed name heard in it (a session in which only
            # people or unconfirmed names were heard says nothing to start from).
            cur = await scope.conn.execute(
                "WITH heard AS (SELECT h.entity_id, h.session_started_at, h.times"
                "   FROM memory_heard h JOIN memory_entities e ON e.guild_id = h.guild_id"
                "   AND e.campaign_id = h.campaign_id AND e.id = h.entity_id"
                "   WHERE h.guild_id = %s AND h.campaign_id = %s"
                "   AND e.status = 'confirmed' AND e.type <> 'person')"
                " SELECT entity_id, sum(times)::int AS n FROM heard"
                " WHERE session_started_at = (SELECT max(session_started_at) FROM heard)"
                " GROUP BY entity_id ORDER BY n DESC, entity_id LIMIT %s",
                (*scope.ids, SEEDS),
            )
            seeds = [str(r["entity_id"]) for r in await cur.fetchall()]
            cur = await scope.conn.execute(
                "SELECT max(session_started_at) AS at FROM memory_heard"
                " WHERE guild_id = %s AND campaign_id = %s",
                scope.ids,
            )
            last = await cur.fetchone()
            since = int(last["at"]) if last and last["at"] is not None else 0
            cur = await scope.conn.execute(
                "SELECT e.name, s.condition FROM memory_states s"
                " JOIN memory_entities e ON e.guild_id = s.guild_id"
                "   AND e.campaign_id = s.campaign_id AND e.id = s.entity_id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.closed_at IS NULL"
                " AND s.condition = ANY(%s) AND e.status = 'confirmed' AND s.created_at >= %s"
                " ORDER BY s.created_at DESC, s.id LIMIT %s",
                (*scope.ids, list(CONDITION_WORDS), since, CONDITION_LINES),
            )
            conditions = [(str(r["name"]), str(r["condition"])) for r in await cur.fetchall()]
            # A cheap look first: only a thread has one of these states, so a campaign with none
            # (most of them) skips the full threads read and costs this note a millisecond.
            cur = await scope.conn.execute(
                "SELECT 1 FROM memory_states WHERE guild_id = %s AND campaign_id = %s"
                " AND closed_at IS NULL AND condition = ANY(%s) LIMIT 1",
                (*scope.ids, list(LIVE_STATES)),
            )
            has_threads = await cur.fetchone() is not None
        sentences: list[str] = []
        if seeds:
            found = await self._recall.recall(
                guild_id,
                campaign_id,
                seeds=seeds,
                audience=DM_SCREEN,
                budget=RECALL_BUDGET,
                hops=1,
            )  # no one is passed as recorded: no transcript line is read or quoted
            sentences = [i.text for i in found.items if i.kind == FACT]
        waiting = await self._claims.waiting_for_check(guild_id, campaign_id, opted_in=opted_in)
        title, day = None, None
        if self._recaps is not None:
            recap = await self._recaps.bridge(guild_id, campaign_id, now + 1)
            if recap is not None and recap.title:
                title, day = recap.title, recap.game_day
        # One read of the campaign's threads (the same one the cards use). A failure here costs
        # the line, never the note.
        quiet: list[str] = []
        due: list[Due] = []
        if has_threads:
            try:
                every = await self._memory.threads(
                    guild_id, campaign_id, stalled_after=stalled_after
                )
                stalled = sorted(
                    (t for t in every if t.stalled),
                    key=lambda t: (-t.quiet_sessions, t.thread.name),
                )
                quiet = [t.thread.name for t in stalled]
                # The same result: promises the DM put in "Due soon", longest quiet first.
                soon = sorted(
                    (
                        t
                        for t in every
                        if t.thread.type == PROMISE and t.state and t.state.condition == GOING
                    ),
                    key=lambda t: (-t.quiet_sessions, t.thread.name),
                )
                due = [
                    Due(
                        t.thread.name,
                        ", ".join(e.name for e in t.promised_by),
                        ", ".join(e.name for e in t.promised_to),
                        t.due,
                    )
                    for t in soon
                ]
            except Exception:
                log.warning("Couldn't read the threads for the pre-session note", exc_info=True)
        return compose(Facts(title, day, sentences, conditions, waiting, quiet, due))
