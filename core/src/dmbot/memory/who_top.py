"""DMbot's top answers for a waiting name on the DM's card (#3603): "a.k.a. Superman, a character"
or "new NPC", best first, at most five. The first are the AI's stored answers for the name
(`who_answers`, best first), then the entries `who_ranking` finds by code, so a card has choices
even when no AI answered. The words of one answer are worked out here once (`label`), and the
website's card reads them from the same function, so the two screens say the same.

DM-only: a hidden entry is a choice only when the caller says the viewer is a DM (`include_hidden`),
and nothing here reads a transcript or logs a name. Pure except `top_for`, which reads one
campaign's entries and stored answers.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from dmbot.memory import descriptions, tags
from dmbot.memory import who_ranking as ranking
from dmbot.memory.kinds import KIND_LABELS
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, Entity
from dmbot.memory.who_answers import ENTITY, NEW, StoredAnswer, WhoAnswers
from dmbot.memory.who_step import candidates_from

if TYPE_CHECKING:
    from dmbot.memory.card_summaries import CardSummaries
    from dmbot.memory.store import MemoryStore

CONFIDENCE_WORDS = {
    "sure": "DMbot is sure",
    "likely": "DMbot thinks so",
    "maybe": "DMbot is not sure",
}
log = logging.getLogger(__name__)

TOP = 5  # the answers a card offers (`who_ranking.LIMIT`)
# The kinds a new entry can be, as the card's own kind names (`ui.names.KINDS`) say them.
CARD_KIND = {"character": "npc", "place": "place", "faction": "faction", "item": "item",
             "event": "event"}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Top:
    """One answer on the card: another name for `target_id`, or a new entry of `new_kind` (a key
    of `KIND_LABELS`)."""

    label: str
    target_id: str | None = None
    new_kind: str | None = None
    confidence: str = ""


def kind_word(kind: str) -> str:
    """A kind in plain words with its article ("a character", "an item")."""
    word = KIND_LABELS.get(kind, kind)
    return f"a {word}" if word and word[0] not in "aeiou" else f"an {word}"


DETAIL_MAX = 60  # a choice's detail, cut at a word, so the label fits a menu


def label(
    *,
    new_kind: str | None = None,
    name: str = "",
    kind: str = "",
    tie: str | None = None,
    detail: str = "",
) -> str:
    """The words on one answer: a new entry of a kind, or another name for an entry that is already
    in the story. An entry whose name only describes it ("the woman") carries the one entry it is
    tied to ("the woman, with Mira"), so two of them can be told apart (#3431). `detail` is the
    entry's most significant detail ("the innkeeper of Bryn Shander"), which says more than its kind
    and so replaces it (`detail_of`)."""
    if new_kind is not None:
        return f"new {KIND_LABELS.get(new_kind, new_kind)}" if new_kind else "new entry"
    if tie and descriptions.is_description(name):
        return f"a.k.a. {name}, with {tie}"
    if detail:
        return f"a.k.a. {name}, {detail}"
    return f"a.k.a. {name}, {kind_word(kind)}"


def detail_of(name: str, summary: str | None, tie_phrase: str) -> str:
    """An entry's most significant detail: the card's one sentence where there is one (without its
    own first words if they are the name), else its strongest connection, else "" (the label then
    says the kind)."""
    sentence = " ".join((summary or "").split()).rstrip(".")
    if sentence.casefold().startswith(name.casefold()):
        sentence = sentence[len(name) :].lstrip(" ,:;-")
    if sentence:
        if len(sentence) > DETAIL_MAX:
            sentence = sentence[: DETAIL_MAX - 1].rsplit(" ", 1)[0].rstrip(",;: ") + "…"
        return sentence
    return tie_phrase


def _from_entity(e: Entity, tie: str | None = None) -> str:
    return label(name=e.name, kind=e.type, tie=tie)


async def top_for(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    entity: Entity,
    *,
    opted_in: Collection[int],
    include_hidden: bool,
    avoid: Collection[str] = (),
    summaries: CardSummaries | None = None,
) -> list[Top]:
    """The card's answers for `entity`, best first. `avoid`: entries that were just said to be
    wrong ("That's wrong" on DMbot's own join), left out. Empty when nothing links the name to an
    entry and no answer was stored."""
    out: list[Top] = []
    seen: set[str] = set(avoid)
    lookup = CampaignLookup.build(await memory.lookup_data(guild_id, campaign_id))
    stored = (
        await WhoAnswers(memory).of(guild_id, campaign_id, [entity.id], opted_in=opted_in)
    ).get(entity.id, [])
    for a in stored:
        if len(out) >= TOP:
            break
        top = _stored(a, lookup, seen, include_hidden)
        if top is not None:
            out.append(top)
    if len(out) < TOP:
        candidates = candidates_from(
            lookup, include_hidden=include_hidden, leave_out=[entity.id, *avoid]
        )
        ask = ranking.Ask(entity.name, entity.type)
        for r in ranking.rank(ask, candidates, include_hidden=include_hidden):
            if len(out) >= TOP:
                break
            if r.candidate.id in seen:
                continue
            seen.add(r.candidate.id)
            e = lookup.entities[r.candidate.id]
            out.append(Top(_from_entity(e), target_id=e.id))
    return await _with_details(memory, guild_id, campaign_id, lookup, out, summaries)


async def _with_details(
    memory: MemoryStore,
    guild_id: int,
    campaign_id: str,
    lookup: CampaignLookup,
    out: list[Top],
    summaries: CardSummaries | None,
) -> list[Top]:
    """Each entry choice's most significant detail (#3603): the card's sentence when
    `DMBOT_CARD_SUMMARY` has one (`summaries` is None when it is off), else its strongest
    connection, else the kind. A read that fails leaves the kind: a choice never fails for it."""
    ids = [t.target_id for t in out if t.target_id]
    if not ids:
        return out
    try:
        ties = await memory.tag_ties(guild_id, campaign_id, ids)
        said = await summaries.get_many(guild_id, campaign_id, ids) if summaries else {}
    except Exception as exc:  # only the type: nothing here holds a name
        log.warning("Top answers: details could not be read (%s)", type(exc).__name__)
        return out
    done: list[Top] = []
    for t in out:
        e = lookup.entities.get(t.target_id) if t.target_id else None
        if e is None:
            done.append(t)
            continue
        found = said.get(e.id)
        detail = detail_of(
            e.name, found.who if found else None, tags.detail_for(ties.get(e.id, ()))
        )
        done.append(replace(t, label=label(name=e.name, kind=e.type, detail=detail)))
    return done


def _stored(
    a: StoredAnswer, lookup: CampaignLookup, seen: set[str], include_hidden: bool
) -> Top | None:
    """One stored answer as a card choice, or None when it can't be one now (the entry has gone,
    is not confirmed, was already offered, or is hidden from this viewer)."""
    if a.pick == NEW and a.new_kind in CARD_KIND:
        return Top(label(new_kind=a.new_kind), new_kind=a.new_kind, confidence=a.confidence)
    if a.pick != ENTITY or not a.target_id or a.target_id in seen:
        return None
    e = lookup.entities.get(a.target_id)
    if e is None or e.status != CONFIRMED or e.merged_into is not None or e.unnamed:
        return None
    if a.target_id in lookup.hidden and not include_hidden:
        return None
    seen.add(a.target_id)
    return Top(_from_entity(e), target_id=e.id, confidence=a.confidence)
