"""Keeping every waiting connection of a backstory at once, and taking them back (#2642).

The DM wrote or uploaded the text, so alongside the one-by-one cards ("Keep this?") they may keep
all the connections that are ready in one press. Each is kept exactly as a single press keeps it:
the DM's word, confirmed, unrevealed (a backstory connection waits to be said at the table, #2404),
and secret when the text wrote it with a secret spelling or an end is audience-limited; pinned as
100% truth when the upload was said to be. Secret ones are never made public here. Every kept
connection is its own change in the campaign's log, and `undo_all` reverses them and puts them back
in the waiting list, so Undo is whole. The same functions serve the website's door.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass, field

from dmbot.memory import kin, knows_how, lore_knowledge, route_length, route_words, secret_questions
from dmbot.memory import reputation_words as feeling_words
from dmbot.memory.backstory_links import BackstoryLinks, NewLink, Waiting
from dmbot.memory.checks import conflicts
from dmbot.memory.clues import KNOWS as KNOWS_PREDICATE
from dmbot.memory.hand_over import SALES
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    BACKGROUND_SOURCE,
    CONFIRMED,
    DM,
    PLAYER_CHARACTER,
    REJECTED,
    UPLOAD_OWN_WRITING,
    MemoryRuleError,
    Relation,
    knows_rank,
    name_key,
)
from dmbot.memory.ontology import DISGUISED_AS, Ontology
from dmbot.memory.store import MemoryStore, refusal_for_log

log = logging.getLogger(__name__)


@dataclass(slots=True)
class KeptAll:
    kept: list[tuple[Waiting, int | None]] = field(default_factory=list)  # (link, batch)
    # the batches that ended an old connection for each kept change (same order as `kept`)
    ended: list[list[int | None]] = field(default_factory=list)
    # the batches written on top of the kept connection itself (its stated length, #4255), taken
    # back before it, since Undo refuses a row changed since (same order as `kept`)
    on_top: list[list[int | None]] = field(default_factory=list)
    refused: int = 0  # the rules refused it: it stays waiting, to be answered by hand
    worked_out: int = 0  # every worked-out family link waiting for the DM's yes after this keep


def kept_how(c: Waiting, old: Sequence[Relation]) -> str:
    """Whether the two people have met, for a kept lore connection (#3881): what its quote says,
    or what the connection it replaces already said, whichever is higher (it only moves up: a
    feeling changed by the lore never takes a meeting away). "" for anything but a `knows`."""
    if c.predicate != KNOWS_PREDICATE:
        return ""
    found = [knows_how.how_from(c.quote, a=c.subject, b=c.object)]
    found += [r.knows_how for r in old if r.predicate == KNOWS_PREDICATE]
    return max(found, key=knows_rank)


def replaced_by(onto: Ontology, link: Waiting, existing: Sequence[Relation]) -> list[Relation]:
    """The live connections between the same two entries that `link` cannot hold beside (friend
    or ally against enemy): keeping it is a change, and these are what it ends (#3021). A
    compatible kind ("knows" beside "works for") replaces nothing. Pure, so the card, the summary
    count and both keep paths read it the same way."""
    pred = onto.predicates.get(link.predicate)
    pair = {link.subject_id, link.object_id}
    if pred is None:
        return []
    handed = hand_over_ties(link, existing)
    # A feeling on "knows" (#3175) is what friend and enemy used to say: against a friendship
    # stored the old way, "enemies" is a change, and the other way round.
    opposite = (
        feeling_words.opposite_old_kind_of(link.detail) if link.predicate == "knows" else None
    )
    return [
        r
        for r in existing
        if r.to_session_at is None
        and r.status != REJECTED
        and {r.subject_id, r.object_id} == pair
        and (
            conflicts(onto, pred, r.predicate)
            or r.predicate == opposite
            or _feeling_changed(link, r)
        )
    ] + handed


def hand_over_ties(link: Waiting, existing: Sequence[Relation]) -> list[Relation]:
    """What a hand-over ends (#4115; Supervisor's ruling, 2026-11-10): the one it came from's live
    `owns` and `carries` of the item after a sale, a gift or a trade; only their `carries` after a
    theft or a loss (they still own it). Never a "found" or any other tie. Pure."""
    if not link.hand_over or not link.hand_over_from:
        return []
    kinds = ("owns", "carries") if link.hand_over in SALES else ("carries",)
    return [
        r
        for r in existing
        if r.to_session_at is None
        and r.status != REJECTED
        and r.subject_id == link.hand_over_from
        and r.object_id == link.object_id
        and r.predicate in kinds
    ]


def handed_away(link: Waiting, existing: Sequence[Relation]) -> Relation | None:
    """The kept hand-over that already took this item from `link`'s holder (#4170): `link` is
    their own `owns` or `carries`, kept after the sale was (the same upload's "Claude carries
    it", kept second). Then it is kept already ended, as the sale would have ended it. The kinds
    are `hand_over_ties`' (after a theft or a loss they still own it). None when there is none, or
    `link` is a hand-over itself. Pure."""
    if link.hand_over or link.predicate not in ("owns", "carries"):
        return None
    for r in existing:
        if (
            r.hand_over
            and r.hand_over_from == link.subject_id
            and r.object_id == link.object_id
            and r.status == CONFIRMED
            # (ended or not: a sale the item has moved on from since still took it from them)
            and link.predicate in (("owns", "carries") if r.hand_over in SALES else ("carries",))
        ):
            return r
    return None


def late_end(
    link: Waiting, existing: Sequence[Relation], *, hidden: bool, start: int | None
) -> tuple[int | None, str | None, Relation | None]:
    """How `link` is kept when a kept hand-over already took the item (`handed_away`): its end
    and reason, or, when the sale is held back from the players and this tie is not (`hidden`
    False), no end and the sale to ask about, as `end_replaced` asks (a tie the players see never
    ends by a sale they don't know of: `whereabouts`' rule). `start`: where the kept tie begins,
    so its end is never before it. Returns (when, reason, the sale to ask about): a reason to end it
    then, or the sale to ask "Did this change?" about, ending it then if the DM says so; (None,
    None, None) when nothing took it."""
    sale = handed_away(link, existing)
    if sale is None:
        return None, None, None
    at = sale.from_session_at if sale.from_session_at is not None else sale.created_at
    # (a tie that starts as another ends, `change_start`, ends after it starts, never with it)
    at = max(at, start + 1) if start is not None else at
    if _hidden(sale) and not hidden:
        return at, None, sale
    return at, sale.hand_over, None


def _hidden(r: Relation) -> bool:
    return r.secret or r.unrevealed


def _feeling_changed(link: Waiting, held: Relation) -> bool:
    """A held "knows" the same way round whose feeling reads differently from the waiting one's
    (#3175): hostile now, friendly in the text. The same feeling (or none against none) is no
    change; a feeling where there was none is one (the word is added)."""
    if link.predicate != "knows" or held.predicate != "knows":
        return False
    if (held.subject_id, held.object_id) != (link.subject_id, link.object_id):
        return False
    return feeling_words.sign_of(held.detail) != feeling_words.sign_of(link.detail)


async def changes_among(
    memory: MemoryStore, guild_id: int, campaign_id: str, links: Sequence[Waiting]
) -> dict[tuple[str, str, str], list[Relation]]:
    """Of these waiting connections, the ones that are changes: (subject, kind, object) -> the
    live connections each would end. Read again every time, never kept, so a connection the DM
    ended or added since is taken into account."""
    if not links:
        return {}
    onto = await memory.ontology(guild_id, campaign_id)
    existing = await memory.relations(guild_id, campaign_id, include_secret=True)
    found = {
        (c.subject_id, c.predicate, c.object_id): replaced_by(onto, c, existing) for c in links
    }
    return {k: v for k, v in found.items() if v}


async def player_character_ids(memory: MemoryStore, guild_id: int, campaign_id: str) -> list[str]:
    """The campaign's confirmed player characters (entry ids), all of them."""
    lookup = CampaignLookup.build(await memory.lookup_data(guild_id, campaign_id))
    return sorted(
        e.id
        for e in lookup.entities.values()
        if e.role == PLAYER_CHARACTER and e.status == CONFIRMED and not e.merged_into
    )


async def player_character_keys(
    memory: MemoryStore, guild_id: int, campaign_id: str
) -> frozenset[str]:
    """The name keys a player character is written by in a text (#3238): its main name and every
    confirmed public spelling, whole (never a first word alone: an NPC called "Hunter" is not the
    player character "Hunter Black"). Names only; nothing about who plays them."""
    lookup = CampaignLookup.build(await memory.lookup_data(guild_id, campaign_id))
    mine = {
        e.id
        for e in lookup.entities.values()
        if e.role == PLAYER_CHARACTER and e.status == CONFIRMED and not e.merged_into
    }
    keys = {n.key for n in lookup.names if n.entity_id in mine and not n.secret and not n.person}
    keys |= {name_key(lookup.entities[i].name) for i in mine}
    return frozenset(keys)


async def shown_entry_ids(memory: MemoryStore, guild_id: int, campaign_id: str) -> set[str]:
    """The entries the players are shown: what the table holds now, by the gate (#4823; party
    knowledge slice 5, never the flags), confirmed, not merged and not an unnamed person (a
    person the story never named is not a name the table can be told of). An entry the DM hid but
    the whole table holds (rows written after the hide, a 👥 Some know naming every member) counts:
    the gate masks it while hidden, and a connection kept to it reaches the players once the DM
    shows it, as rule 2 says. Read once per batch."""
    held = await memory.entries_shown_to_table(guild_id, campaign_id)
    data = await memory.lookup_data(guild_id, campaign_id)
    return {
        e.id
        for e in data.entities
        if e.id in held and e.status == CONFIRMED and not (e.unnamed or e.merged_into)
    }


def kept_unrevealed(c: Waiting, secret: bool, shown: set[str]) -> bool:
    """Does a connection kept from this waiting row still wait to be said at the table? Yes,
    unless the upload said the players know it (#2959: World general knowledge, or a smart guess
    the AI called common knowledge), it is not secret and the table holds both its ends
    (`shown_entry_ids`, the gate's answer, #4823)."""
    # (`c.secret`, a limited clue's end included: what only some know is never shown by a keep,
    # failing closed, even when the whole table holds it; ruling (a) on #4823)
    if not c.world or secret or c.secret:
        return True
    return not {c.subject_id, c.object_id} <= shown


async def mark_guess(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    kind: str,
    written_ids: Sequence[str],
    *,
    made_since: int,
) -> None:
    """A statement kept because the AI guessed the players know it is recorded at 0.75, not as the
    DM's word: only the rows written from `made_since` (the keep's own start, #4524), never a
    player character already sure of it. Any failure is logged: the connection is kept either
    way."""
    if not written_ids:
        return
    try:
        await memory.mark_guess(
            guild_id, campaign_id, kind, written_ids, lore_knowledge.GUESS_CERTAINTY,
            source=DM, made_since=made_since,
        )  # fmt: skip
    except Exception:  # a database trouble: the knowing stays at sure, the keep stays made
        log.exception("A guess could not be recorded as a guess")


async def common_connection(
    memory: MemoryStore, guild_id: int, campaign_id: str, c: Waiting, made: Relation, now: int
) -> int | None:
    """A kept connection the upload calls world knowledge (a World read, or a smart guess's
    "world" statement) is common knowledge (#4537): known to every character, now or later, at the
    guess's certainty for a guess. Only one the players are shown (the gate counts it only while
    its flags show it), and only one made by this keep (`now`, its start): an older twin's knowers
    are the DM's. Its batch, for the keep's Undo; never a reason to lose the connection."""
    if c.world not in (lore_knowledge.SURE, lore_knowledge.GUESS) or made.secret or made.unrevealed:
        return None
    if made.created_at < now:
        return None
    sure = 1.0 if c.world == lore_knowledge.SURE else lore_knowledge.GUESS_CERTAINTY
    try:
        written = await memory.add_common_knowledge(
            guild_id, campaign_id, "connection", [made.id], sure, source=DM
        )
    except Exception as exc:
        log.warning("A kept connection was not made common knowledge (%s)", type(exc).__name__)
        return None
    return written.batch


def change_start(old: Sequence[Relation], now: int) -> int:
    """When a change takes hold: now, or just after the latest start among the connections it
    ends (one that began later than now would end before it began). The old ones end and the new
    one starts at this same moment, so they never overlap."""
    return max([now, *((r.from_session_at or 0) + 1 for r in old)])


async def end_replaced(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    old: Sequence[Relation],
    start: int,
    ended: list[int | None],
    *,
    held: list[str],
    link: Waiting | None = None,
    hidden: bool = False,
    dm_chose_secret: bool = False,
) -> None:
    """End these connections as of `start` (never deleted, so the history and the timeline keep
    them). Each one's batch is appended to `ended` as it is done, so a caller whose later step
    fails can take back exactly what was ended (`undo_batches`). A hand-over's (`link`, #4115)
    end with its reason; when what is kept is `hidden` (secret, or not said at the table yet), a
    hand-over never ends a tie the players can see: that would tell them the item moved
    (`whereabouts`' rule, Supervisor on #4123).

    Public never becomes secret by itself (#4524, ruling E): when what is kept is `hidden` and an
    old connection is one the players can see, it is not ended: its id goes in `held`, and once the
    new one is kept the caller asks the DM "Did this change? Keep both / End the old one"
    (`ask_held`), so a keep that fails asks nothing. The DM's own hide on the card
    (`dm_chose_secret`: 🤫, or Players: hide) ends it as before."""
    handed = {r.id for r in hand_over_ties(link, old)} if link is not None else set()
    for r in old:
        reason = link.hand_over if link is not None and r.id in handed else None
        if r.id in handed and hidden and not _hidden(r):
            continue
        if hidden and not _hidden(r) and not dm_chose_secret:
            held.append(r.id)
            continue
        written = await memory.update_relation(
            guild_id, campaign_id, r.id, source=DM, to_session_at=start, end_reason=reason
        )
        ended.append(written.batch)


async def ask_held(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    held: Sequence[str],
    *,
    by: str,
    start: int,
    label: str,
    batch: int | None,
) -> None:
    """The kept connection `by` would have ended these public ones (`end_replaced`'s `held`): one
    "Did this change?" question each, with the upload as evidence, tied to the keep's `batch` so
    an Undo of the keep takes it back. Never a reason to lose the keep: a failure is logged by its
    type."""
    for relation_id in held:
        try:
            await memory.ask_replace(
                guild_id, campaign_id, relation_id, by=by, end_at=start, label=label, batch=batch
            )
        except Exception as exc:
            log.warning("A replace question could not be asked (%s)", type(exc).__name__)


async def undo_batches(
    memory: MemoryStore, guild_id: int, campaign_id: str, batches: Sequence[int | None]
) -> bool:
    """Take back these writes, newest first. One that cannot be reversed (changed since, too
    late, already undone) is logged and skipped: the rest still go back. True when every one
    went back, so a caller can say when only part of it did."""
    every = True
    for batch in reversed(batches):
        if batch is None:
            continue
        try:
            await memory.undo(guild_id, campaign_id, batch, source=DM)
        except MemoryRuleError:
            every = False
            log.info("A write of a change could not be taken back")
    return every


async def keep_guesses(
    memory: MemoryStore, guild_id: int, campaign_id: str, c: Waiting, made: Relation, now: int
) -> None:
    """DMbot's guesses of which NPCs and groups know a connection the DM just kept (#2959 slice 3),
    written as knowers below 1.0. Only for a connection made just now (an older twin's knowers are
    the DM's), and never a reason to lose the connection: a failure is logged by its type."""
    if not c.npc_guesses or made.created_at < now:
        return
    try:
        await memory.add_guessed_knowers(
            guild_id, campaign_id, "connection", made.id, c.npc_guesses, source=DM
        )
    except Exception as exc:
        log.warning("DMbot's guesses for a kept connection were not kept (%s)", type(exc).__name__)


async def keep_all(
    memory: MemoryStore,
    store: BackstoryLinks,
    guild_id: int,
    campaign_id: str,
    links: Sequence[Waiting],
    progress: Callable[[int, int], Awaitable[None]] | None = None,
    *,
    keep_flags: bool = True,
    one_press: bool = False,
    players: str = "",
    listed: Collection[str] = (),
    knowers: Sequence[str] = (),
) -> KeptAll:
    """Keep each of these ready connections. A refused one stays in the list. A connection DMbot
    inferred (#2959 item 5) is never kept by Keep all or the sure step: only by the DM's press on
    that one (`one_press`), so it is left out and stays waiting (not counted as refused). The one
    exception is `listed`, the waiting ids the sure step found to be a region's listed towns
    (#4636 part 3, `auto_accept.listed_towns`).
    `progress(done, total)` is told before the first and after each one (kept or refused: tried),
    for the website's "12 of 50" (#2924); counts only. `keep_flags`: keeping a connection that
    already exists never makes it secret, so a secret spelling in the text never hides what the
    players were shown (#3025); False only when the DM pressed 🤫 Keep as secret, their own word.
    `players` (one press only, #4432): what the DM chose about the players on the card: "hide",
    "show", or "" for how it is now (a connection it replaces that is hidden stays hidden, one the
    players see stays seen); a secret name always stays hidden, whatever was chosen.
    `knowers` (one press only, #4680): 👥 Some know, as Discord's picker saves it: the connection
    is kept held back from the players, never secret, and these characters know it (a connection
    that already existed keeps the characters it had, as Discord's card does), as the DM's own
    choice. A connection with a secret name or a hand-over is refused whole, as on the
    card.
    """
    done = KeptAll()
    if one_press and len(links) != 1:
        raise ValueError("One press keeps one connection.")
    if knowers and not one_press:
        raise ValueError("Some know is one press.")
    if not one_press:
        # (nor a hand-over, #4115: it ends what someone else holds, so it is seen before it is kept)
        links = [
            c for c in links if (not c.inferred or (listed and c.id in listed)) and not c.hand_over
        ]
    elif players not in ("", "hide", "show"):
        raise ValueError("players is hide, show or empty.")
    total = len(links)
    if progress is not None:
        await progress(0, total)
    onto = await memory.ontology(guild_id, campaign_id) if links else None
    shown = await shown_entry_ids(memory, guild_id, campaign_id) if links else set()
    for tried, c in enumerate(links, 1):
        if knowers and (c.secret or c.hand_over):
            # 👥 Some know is never for a secret name or a hand-over: the card says so and writes
            # nothing (Discord's SOME_KNOW_REFUSED), so here it is refused whole.
            done.refused += 1
            if progress is not None:
                await progress(tried, total)
            continue
        # (a 🤫 question a keep asks cites this connection's upload: #4524)
        with secret_questions.evidence(c.upload or ""):
            now = int(time.time())
            rels = await memory.relations(guild_id, campaign_id, include_secret=True)
            old = replaced_by(onto, c, rels) if onto is not None else []
            ended: list[int | None] = []
            held: list[str] = []
            on_top: list[int | None] = []
            start = change_start(old, now)
            # (the kept row: its own secret, and the table's view by the gate; a limited clue's end
            # is the card's alone, ruling (a) on #4823)
            secret = c.own_secret
            unrevealed, flags = kept_unrevealed(c, secret, shown), keep_flags
            pair = [r for r in old if {r.subject_id, r.object_id} == {c.subject_id, c.object_id}]
            if knowers:
                # 👥 Some know (#4680): held back from the players, never secret, and exactly the
                # ticked characters know it; the DM's own choice, as Discord's picker makes it.
                secret, unrevealed, flags = False, True, False
            elif one_press and not c.secret and (players or pair):
                # the card's Players line (#4432): the choice, else how the connection it replaces
                # is
                now_hidden = bool(pair) and all(r.secret or r.unrevealed for r in pair)
                hide = players == "hide" or (not players and now_hidden)
                if hide:
                    keeps_secret = players == "hide" or any(r.secret for r in pair)
                    secret, unrevealed = keeps_secret, not keeps_secret
                    flags = flags and players != "hide"  # the DM's own hide beats an old twin
                else:
                    secret, unrevealed = False, False
                    flags = flags and not now_hidden  # a shown one is not hidden by an old flag
            # (the same upload's sale kept first took the item already, #4170)
            late_at, late_why, late_ask = late_end(
                c, rels, hidden=secret or unrevealed, start=start if old else None
            )
            try:
                await end_replaced(
                    memory, guild_id, campaign_id, old, start, ended, held=held, link=c,
                    hidden=secret or unrevealed,
                # (the same choice as the keep's own below: 🤫 Keep as secret, or Players: hide)
                dm_chose_secret=not keep_flags or players == "hide" or bool(knowers),
                )  # fmt: skip
                written = await memory.add_relation(
                    guild_id, campaign_id, c.subject_id, c.predicate, c.object_id,
                    source=DM, confidence=1.0, status=CONFIRMED, secret=secret, detail=c.detail,
                    directional=kin.directional(c.detail),  # a family role keeps who holds it
                    unrevealed=unrevealed, decided=c.decided or None,
                    # the uploader's character knows it, unless it uses a secret name (#2935)
                    # (a disguise is secret by nature, yet its character knows it, #3091)
                    known_by=(
                        list(knowers)
                        if knowers
                        else [c.known_by]
                        if c.known_by and (not secret or c.predicate == DISGUISED_AS)
                        else ()
                    ),
                    from_session_at=start if old else None,  # (starts as the old one ends)
                    to_session_at=late_at if late_why else None, end_reason=late_why,
                    hand_over=c.hand_over, hand_over_from=c.hand_over_from,
                    keep_flags=flags,
                    # (🤫 Keep as secret or Players: hide is the DM's own word, #4524)
                    dm_chose_secret=not keep_flags or players == "hide" or bool(knowers),
                    # whether they've met, from the quote's own words (#3881)
                    knows_how=kept_how(c, old),
                    # (the uploader's character knows it from its own backstory, #4795; a
                    # 👥 Some know pick is the DM's, not a backstory's)
                    backstory_of=c.known_by if c.known_by and not knowers else None,
                )  # fmt: skip
            except Exception as exc:
                # the change is whole or not at all, whatever went wrong
                await undo_batches(memory, guild_id, campaign_id, ended)
                if not isinstance(exc, MemoryRuleError):
                    raise
                done.refused += 1
                # Why, as the one-card Keep logs it (#4161): the rule's own sentence when it is one
                # of the fixed ones, never a campaign's words, and the connection's kind
                log.info(
                    "A connection from a background was refused (Keep all): %s (%s)",
                    refusal_for_log(exc),
                    c.predicate,
                )
                if progress is not None:
                    await progress(tried, total)
                continue
            made = written.value[0]
            await ask_held(
                memory, guild_id, campaign_id, held, by=made.id, start=start,
                label=c.upload or "", batch=written.batch,
            )  # fmt: skip
            if late_ask is not None and late_at is not None:
                await ask_held(
                    memory, guild_id, campaign_id, [made.id], by=late_ask.id, start=late_at,
                    label=c.upload or "", batch=written.batch,
                )  # fmt: skip
            # Where it came from, for the graph's line panel (#2946): only a connection made just
            # now (an older twin's rows are not this upload's), and never a reason to lose the
            # connection.
            if c.upload and made.created_at >= now:
                try:
                    # (queued only by the 📜 read or the website's lore upload: own writing, #3742)
                    await memory.record_link_source(
                        guild_id, campaign_id, made.id, c.upload, c.quote, inferred=c.inferred,
                        kind=UPLOAD_OWN_WRITING,
                    )  # fmt: skip
                except Exception as exc:
                    log.warning(
                        "A kept connection's source could not be stored (%s)", type(exc).__name__
                    )
            await keep_guesses(memory, guild_id, campaign_id, c, made, now)
            # A feeling kept with a "knows" (#3175) is how the first name feels about the second:
            # the DM's press stores it as a reputation from the story (a standing the DM set still
            # wins). Its batch joins the ones Undo takes back, so one press and one Undo cover both.
            word = feeling_words.word_of(c.detail) if c.predicate == "knows" else None
            score = feeling_words.score_of(word) if word is not None else None
            if score is not None:
                try:
                    batch = await memory.add_feeling(
                        guild_id, campaign_id, c.subject_id, c.object_id, score
                    )
                except Exception as exc:  # the connection is kept whatever happens to the feeling
                    log.warning(
                        "A kept connection's feeling could not be stored (%s)", type(exc).__name__
                    )
                    batch = None
                if batch is not None:
                    ended.append(batch)
            # A route's stated length, from the route's own quote by set phrases only (#4255): kept
            # as DMbot's read (only the DM's ✅ makes it what players may see), in the same Undo.
            # Only on a route made just now: an older twin's length is the DM's or an earlier read's
            if (
                made.predicate == route_words.ROUTE_TO
                and not made.route_state
                and c.quote
                and made.created_at >= now
            ):
                words = route_length.find(c.quote, places=(c.subject, c.object))
                if words:
                    try:
                        said = await memory.set_route_length(
                            guild_id,
                            campaign_id,
                            made.id,
                            words,
                            on="connection",
                            source=BACKGROUND_SOURCE,
                        )
                        on_top.append(said.batch)
                    except Exception as exc:  # the connection is kept whatever the length does
                        log.warning(
                            "A kept route's length could not be stored (%s)", type(exc).__name__
                        )
            # (only a connection made just now: an older twin's rows are the DM's, not this guess's)
            if c.world == lore_knowledge.GUESS and not made.unrevealed and made.created_at >= now:
                await mark_guess(
                    memory, guild_id, campaign_id, "connection", [made.id], made_since=now
                )
            on_top.append(await common_connection(memory, guild_id, campaign_id, c, made, now))
            if c.truth and not c.inferred:  # (DMbot's inference is never pinned as 100% truth)
                try:
                    await memory.pin(guild_id, campaign_id, written.value[0].id, True, source=DM)
                except MemoryRuleError:  # kept all the same: pinning is one tap on its card
                    log.info("A kept connection could not be pinned")
            await store.remove(guild_id, campaign_id, c)
            done.kept.append((c, written.batch))
            done.ended.append(ended)
            done.on_top.append(on_top)
            if progress is not None:
                await progress(tried, total)
    done.worked_out = await rework_family(memory, guild_id, campaign_id, [c for c, _ in done.kept])
    return done


async def rework_family(
    memory: MemoryStore, guild_id: int, campaign_id: str, kept: Sequence[Waiting]
) -> int:
    """Work out the family links that follow from what was just kept (#3238): without this they
    appeared only when the DM next opened the connections screen or a session ended, so a DM who
    kept a backstory's family links and looked for the suggestions found none. Only when a family
    link was kept. Returns how many wait for the DM's yes (counts only); a failure is logged and
    leaves the kept links as they are, since the suggestions are recomputed on the next look."""
    if not any(c.predicate == "kin_of" for c in kept):
        return 0
    try:
        await memory.rework_family(guild_id, campaign_id)
        return len(await memory.worked_out_waiting(guild_id, campaign_id))
    except Exception as exc:
        log.warning("Worked-out family links could not be refreshed (%s)", type(exc).__name__)
        return 0


async def undo_all(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str, done: KeptAll
) -> int:
    """Take back what `keep_all` kept: each is reversed and waits again. Returns how many went
    back; one that cannot be reversed (changed since) is left as it is."""
    try:
        return await _undo_all(memory, store, guild_id, campaign_id, done)
    finally:
        # a suggestion that followed from a link now taken back goes with it
        await rework_family(memory, guild_id, campaign_id, [c for c, _ in done.kept])


async def _undo_all(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str, done: KeptAll
) -> int:
    back = 0
    rows = list(zip(done.kept, done.ended, done.on_top, strict=True))
    for (c, batch), ended, on_top in reversed(rows):
        if batch is None and not ended:
            continue
        await undo_batches(memory, guild_id, campaign_id, on_top)  # (first: it changed the row)
        # (the old connections come back whatever happens to the new one, and the link waits
        # again once the new one is gone: nothing is lost half way)
        if batch is not None:
            try:
                await memory.undo(guild_id, campaign_id, batch, source=DM)
            except MemoryRuleError:
                continue
        await undo_batches(memory, guild_id, campaign_id, ended)
        await store.add(
            guild_id,
            campaign_id,
            [
                NewLink(
                    c.subject_id,
                    c.predicate,
                    c.object_id,
                    c.quote,
                    c.own_secret,  # (requeued as it was: the limited end is read again)
                    c.detail,
                    c.truth,
                    known_by=c.known_by,
                    world=c.world,
                    # (back with who had it before, so keeping it again ends their part, #4115)
                    hand_over_from=c.hand_over_from,
                    hand_over=c.hand_over,
                )
            ],
        )
        back += 1
    return back


async def keep_assumed(
    memory: MemoryStore, store: BackstoryLinks, guild_id: int, campaign_id: str
) -> int:
    """Keep each place's parent DMbot assumed from where the story was (#4173 slice 3) whose two
    places are both kept now: never a question (Supervisor: "stored, not asked"), so nothing waits
    for a press. Kept with the mark, held back from the players, as one change each. One whose place
    has a parent that was said by now is dropped (the said one stands), and so is a refused one;
    a waiting name leaves it waiting, and a turned-down name takes it away (the queue's own
    clean-up). Returns how many were kept."""
    kept = 0
    for c in await store.waiting(guild_id, campaign_id, assumed=True):
        if not c.ready:
            continue
        try:
            await memory.add_relation(
                guild_id, campaign_id, c.subject_id, c.predicate, c.object_id,
                source=DM, confidence=1.0, status=CONFIRMED, assumed=True,
            )  # fmt: skip
            kept += 1
        except (MemoryRuleError, ValueError):
            # (a parent said meanwhile, one the DM turned down, or an end no longer a place: the
            # guess is not needed, and is dropped so it never fails a later look)
            pass
        await store.remove(guild_id, campaign_id, c)
    return kept
