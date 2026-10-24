"""What the recap stages read for one session (#1382), loaded the same way by the bot's job and
by the command-line test run (#1424), so a test run reads exactly what the live job would.

This campaign's own transcript of that session, its names (secret ones read fresh), its earlier
posted recap and its game clock. None when the names can't be read: then nothing is written,
since a secret name couldn't be kept out.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterator, Sequence
from typing import Any

from dmbot.memory.extractor import readable
from dmbot.memory.models import name_key, spelling_key
from dmbot.memory.secret_aloud import MIN_KEY as MIN_SECRET_LETTERS
from dmbot.recaps import fixed_lines
from dmbot.recaps import split as sp
from dmbot.recaps.pipeline import Inputs
from dmbot.recaps.style import NoteStore, StyleStore
from dmbot.recaps.text import strip_cues
from dmbot.timebot.clock import day_of as clock_day_of

log = logging.getLogger(__name__)


def said_in(lines: Any) -> str:
    """What the table said in a session, for the recap's name check: the consented table lines
    (as heard and as cleaned), never the DM sidebar, and squashed as names are compared."""
    return " ".join(
        name_key(f"{line.heard} {line.text}") for line in lines if not getattr(line, "sidebar", "")
    )


class _EveryName(Collection[str]):
    """ "Every name is recanted": what the recap uses when it cannot tell which secret names the DM
    took back, so no secret name counts as said aloud (stricter, never looser)."""

    def __contains__(self, item: object) -> bool:
        return True

    def __iter__(self) -> Iterator[str]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return True  # (so a test like `if recanted:` never reads "none")


EVERY_NAME = _EveryName()


async def recap_recanted(
    exposures: Any, guild_id: int, campaign_id: str, session_at: int | None
) -> Collection[str]:
    """The secret names the DM took back for that session (#2591): they stay secret in its recap
    even though the table said them. With no store to ask, every name counts as taken back
    (`EVERY_NAME`); a store that fails raises to the caller's guard, never reads as "none"."""
    if session_at is None:
        return frozenset()
    if exposures is None:  # no store to ask: nothing said counts as revealed
        return EVERY_NAME
    return frozenset(await exposures.recanted(guild_id, campaign_id, session_at))


async def recap_said(
    transcripts: Any, guild_id: int, campaign_id: str, session_at: int | None
) -> str | None:
    """What the table said in that session (for names the players haven't been shown yet, #2404),
    or None when there is no session or its lines can't be read: then such a name is not allowed."""
    if session_at is None or transcripts is None:
        return None
    try:
        sessions = await transcripts.sessions(guild_id, campaign_id, limit=1000)
        found = [s for s in sessions if s.started_at == session_at]
        if not found:
            return None
        return said_in(await transcripts.lines(guild_id, found[0].id))
    except Exception:
        log.exception("Recap: couldn't read what the session said")
        return None


def recap_name_sets(
    names: Any, said: str | None, recanted: Collection[str] = ()
) -> tuple[list[str], list[str]]:
    """(the names the recap may use, the names it must not) for a session. A name the players
    haven't been shown yet (#2404) is allowed only when the table said it in this session (`said`,
    from `said_in`): the recap writes what the party knows, and saying a name at the table is how
    the party learns it. A 🤫 secret name the table said aloud is the same (#2591, owner decision
    2026-10-24): only that spelling, and only the name: its secret connections, notes and
    anything else marked secret about it stay out of the writer's input and are checked in the
    draft (`Inputs.identity_ties`). A name the DM took back (`recanted`, spelling keys, #2410) is
    secret again. With no `said` (the session's lines can't be read) nothing is revealed:
    stricter, never looser."""

    def spoken(text: str) -> bool:
        return said is not None and f" {name_key(text)} " in f" {said} "

    def revealed(n: Any) -> bool:
        # (a secret "name" shorter than this is too common a word to count as said)
        return (
            n.secret
            and not getattr(n, "clue", False)  # a clue's names stay secret, said or not
            and len(name_key(n.text)) >= MIN_SECRET_LETTERS
            and spoken(n.text)
            and spelling_key(n.text) not in recanted
        )

    allowed = {
        n.text
        for n in names.names
        if n.confirmed
        and not n.person
        and (revealed(n) if n.secret else (not n.entry_hidden or spoken(n.text)))
    }
    secret = {
        n.text
        for n in names.names
        if (n.secret and not revealed(n))
        or (n.entry_hidden and not n.secret and not spoken(n.text))
    }
    return sorted(allowed), sorted(secret)


def identity_ties(names: Any) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """A 🤫 spelling is secret because of who it really is ("the hooded stranger" is Belleros): the
    link between it and the entry's other names is a secret connection too (#2591). Saying
    both at the table lets both be used, but a sentence that puts them together states the link,
    so each secret spelling is tied to the entry's public names. Clue names (#1649) are not."""
    by_entity: dict[str, list[Any]] = {}
    for n in names.names:
        if n.confirmed and not n.person:
            by_entity.setdefault(n.entity_id, []).append(n)
    ties = []
    for entries in by_entity.values():
        public = tuple(dict.fromkeys(n.text for n in entries if not n.secret))
        for n in entries:
            if n.secret and not getattr(n, "clue", False) and public:
                ties.append(((n.text,), public))
    return ties


def recap_people(
    names: Any, allowed: Collection[str]
) -> tuple[dict[str, str], list[str], dict[int, tuple[str, ...]]]:
    """(another spelling -> the story-memory name, every player character, each player's player
    characters) for a recap (#2573). Only the names the recap may use (`allowed`, so never a secret
    or a not-yet-shown one) and never a table person's name. A DM's correction ("this heard word
    means that name") counts as another spelling when it points at exactly one name."""
    ok = set(allowed)
    entities = getattr(names, "entities", {})  # (a stand-in in a test may have none)
    real = {
        name_key(e.name) for e in entities.values()
    }  # a spelling that is a real name is not an alias
    aliases: dict[str, str] = {}
    for entry in names.names:
        entity = entities.get(entry.entity_id)
        if (
            entity is not None
            and entry.confirmed
            and not entry.secret
            and not entry.person
            and entity.name in ok
            and entry.text != entity.name
            and name_key(entry.text) not in real
        ):
            aliases[entry.text] = entity.name
    for heard, entity_ids in getattr(names, "fixes", {}).items():
        only = {i for i in entity_ids if i in entities}
        if len(only) == 1:
            entity = entities[next(iter(only))]
            if entity.name in ok and heard != name_key(entity.name) and heard not in real:
                aliases.setdefault(heard, entity.name)
    pcs: list[str] = []
    party: dict[int, list[str]] = {}
    for entity in sorted(entities.values(), key=lambda e: e.name):
        if entity.played_by and entity.role == "player_character" and entity.name in ok:
            try:
                player = int(entity.played_by)
            except (TypeError, ValueError):
                continue  # a bad value costs the presence list, never the recap
            pcs.append(entity.name)
            party.setdefault(player, []).append(entity.name)
    return aliases, pcs, {u: tuple(v) for u, v in party.items()}


async def load_inputs(
    *,
    campaigns: Any,
    lookup: Any,
    transcripts: Any,
    recaps: Any,
    clocks: Any | None,
    guild_id: int,
    campaign_id: str,
    session_at: int,
    styles: StyleStore | None = None,
    notes: NoteStore | None = None,
    with_earlier: bool = True,
    importance: Any | None = None,
    recanted: Collection[str] = (),
    relation_ties: Sequence[tuple[tuple[str, ...], tuple[str, ...]]] = (),
) -> Inputs | None:
    campaign = await campaigns.get(guild_id, campaign_id)
    if campaign is None:
        return None
    try:
        names = await lookup.get(guild_id, campaign_id)
    except Exception:
        log.exception("Recap: couldn't load the campaign's names")
        return None
    sessions = await transcripts.sessions(guild_id, campaign_id, limit=1000)
    found = [s for s in sessions if s.started_at == session_at]
    if not found:
        return None
    lines = await transcripts.lines(guild_id, found[0].id)
    bridge = await recaps.bridge(guild_id, campaign_id, session_at)
    earlier = (
        await _earlier(transcripts, recaps, guild_id, campaign_id, session_at)
        if with_earlier
        else None
    )
    stored = await clocks.get(guild_id, campaign_id) if clocks is not None else None
    # How connected each name is in The truth (#2411): ranks the beats, gives the writer nothing.
    scores = await importance.get(guild_id, campaign_id) if importance is not None else {}
    said = said_in(lines)
    allowed_now, secret_now = recap_name_sets(names, said, recanted)
    aliases, pcs, party = recap_people(names, allowed_now)
    return Inputs(
        lines=lines,
        dm_ids=set(campaign.dm_user_ids),
        allowed_names=allowed_now,
        secret_names=secret_now,
        identity_ties=tuple(identity_ties(names)),
        relation_ties=tuple(relation_ties),
        # Last time's recap without its fixed lines: the writer is not shown lines to copy (#2253).
        previous=fixed_lines.without(strip_cues(bridge.summary)) if bridge is not None else "",
        campaign_name=campaign.name,
        earlier=earlier,
        importance=dict(scores),
        aliases=aliases,
        pcs=pcs,
        party=party,
        # Which of the ten closing lines: by this session's place in the campaign (#2253).
        ending=fixed_lines.ending_for(sum(1 for s in sessions if s.started_at < session_at)),
        day=clock_day_of(stored.clock.minute) if stored is not None else None,
        # The campaign's example recaps, or the defaults: for the writer's tone only (#2075).
        style=await styles.examples_for(guild_id, campaign_id) if styles is not None else (),
        notes=[n.text for n in await notes.notes(guild_id, campaign_id)]
        if notes is not None
        else (),
    )


async def _earlier(
    transcripts: Any, recaps: Any, guild_id: int, campaign_id: str, session_at: int
) -> sp.Earlier | None:
    """What the campaign's previous session was about (#2253), for finding a retelling of it at
    the start of this one: the content words of its play and its beat sentences. Only its play, by
    its stored split, so a retelling inside it is not carried on into this one (15 into 16 into
    17); a session from before the split is taken whole. The previous session is the latest one
    before this, including one whose transcript the keep-days limit deleted: then its beats (from
    its recap job, if it had one) stand in for it, and with no job there is nothing to compare
    with, never an older session. None when there is no earlier session at all: then nothing in
    this one is a recap. Read once, for the split stage."""
    previous = await transcripts.latest_before(guild_id, campaign_id, session_at)
    if previous is None:
        return None
    started, deleted = previous
    job = await recaps.earlier_job(guild_id, campaign_id, session_at)
    if job is not None and job.session_at != started:
        job = None  # a recap of an older session: its line numbers are not that transcript's
    beats = sp.beat_sentences(job.beats or []) if job is not None else ()
    if deleted:
        return sp.Earlier(frozenset(), beats)
    found = [
        s
        for s in await transcripts.sessions(guild_id, campaign_id, limit=1000)
        if s.started_at == started
    ]
    if not found:
        return sp.Earlier(frozenset(), beats)
    said = readable(await transcripts.lines(guild_id, found[0].id))
    split = sp.from_json(job.split) if job is not None else None
    if split is not None:
        said = sp.pick(said, split, sp.PLAY)
    return sp.Earlier(sp.vocabulary_of(s.text for s in said), beats)
