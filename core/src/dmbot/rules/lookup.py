"""Finding the answer to a rule lookup (#908, #5156): what `/dmbot rule` says for a typed name,
with no Discord in it, so the website's lookup gives the same card.

The order is the campaign's: its house rules first, then its own homebrew (which shadows the
book's card of the same name), then the target ruleset, then the fallback; older content carries
`[Legacy 2014]`. The book is the packaged SRD index (`rules.index.srd()`, open-licence data
shipped with the code): short excerpts only, nothing is stored, and the typed name is never
logged. `find` reads nothing from a database: the caller hands it the campaign's house rules and
its homebrew entry of that name, so Discord's reads and the website's reads (DM-only doors) both
feed one search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dmbot.campaigns import FALLBACK_NONE, Campaign
from dmbot.rules import index, rule_card
from dmbot.rules.book_rank import book_is_legacy, book_wins
from dmbot.rules.homebrew import Homebrew
from dmbot.rules.house import HouseRule

CARD, HOMEBREW, NONE, DM_ONLY_KIND = "card", "homebrew", "none", "dm_only"
DM_ONLY = "That's one for your DM."  # as a table question's answer (sidebar.answer.ONE_FOR_DM)


@dataclass(frozen=True, slots=True)
class Answer:
    """What a lookup found. `parts`: the card's text in pages, for the person who asked. `close`:
    near names when nothing matched. `copy`: a followed pack's homebrew entry the DM may copy into
    the campaign (Discord's button; None for everything else)."""

    kind: str
    parts: list[str]
    close: list[index.Entry] = field(default_factory=list)
    copy: Homebrew | None = None


_KIND_SUFFIX = re.compile(
    r"^(?P<name>.+) \((?P<kind>"
    r"rule|item|feat|species|background|weapon|armor|gear|tool|poison|trap|curse|disease|creature"
    r")\)$"
)
_SUFFIX_KINDS = {"creature": "monster"}  # how the list says a kind, where it differs


def _named_kind(srd: index.Index, typed: str, campaign: Campaign) -> index.Hit | None:
    """ "Acolyte (background)" or "Acolyte (creature)", as the name list words a name that a
    creature shares with a feat, species or background: the one it says, not the other."""
    match = _KIND_SUFFIX.match(typed)
    if match is None:
        return None
    return srd.lookup(
        match["name"],
        campaign.target_ruleset,
        campaign.fallback_ruleset,
        kind=_SUFFIX_KINDS.get(match["kind"], match["kind"]),
    )


def _asked_for(srd: index.Index, typed: str, target: str, fallback: str) -> index.Hit | None:
    """The glossary entry, else the class feature, the magic item, the feat, the species or the
    background, with this name. A feature several classes share ("evasion") gives the first
    class's; typing the class first ("rogue evasion") picks that class, and a level after it
    ("barbarian improved brutal strike 17") picks the one of that level: which is how the name
    list words them."""
    hit = srd.lookup(typed, target, fallback, kind=index.RULE)
    if hit is not None and not (hit.from_fallback and srd.has_asked_for(typed, target)):
        return hit  # (a 2014 rule gives way to the target's own feat, item ... of the name: #1943)
    found = _features(srd, typed, target, fallback)
    if found:
        return found[0]
    for kind in (index.ITEM, *index.ORIGIN_KINDS, *index.EQUIPMENT_KINDS, *index.HAZARD_KINDS):
        hit = srd.lookup(typed, target, fallback, kind=kind)
        if hit is not None:
            return hit
    return None


def _features(srd: index.Index, typed: str, target: str, fallback: str) -> list[index.Hit]:
    """The class features a typed name leads to: "evasion", "rogue evasion" or
    "barbarian improved brutal strike 17"."""
    words = typed.split()
    klass = ""
    if len(words) > 1 and words[0].lower() in index.CLASS_WORDS:
        klass, words = words[0], words[1:]
    level = int(words.pop()) if len(words) > 1 and words[-1].isdigit() else None
    for name, prefer in ((" ".join(words), klass), (typed, "")):
        found = srd.lookup_all(name, target, fallback, kind=index.FEATURE, prefer=prefer)
        if level is not None:
            found = [h for h in found if h.entry.details.get("level") == level]
        if found:
            return found
    return []


def find(
    campaign: Campaign,
    typed: str,
    rules: list[HouseRule],
    own: Homebrew | None,
    *,
    is_dm: bool,
    kind: str | None = None,
) -> Answer:
    """The answer for `typed` (already trimmed and checked for length) in this campaign. `own`:
    the campaign's homebrew entry with exactly this name, if any (an entry the DM turned off is not
    passed). A player's parts leave out what the DM keeps (`rule_card.for_player`)."""
    srd = index.srd()
    hit = _named_kind(srd, typed, campaign) if kind is None else None
    other = ""  # the card's note about the older creature a newer species or background replaced
    if hit is None:
        hit = srd.lookup(typed, campaign.target_ruleset, campaign.fallback_ruleset, kind=kind)
        if hit is not None and hit.entry.kind == "monster" and hit.from_fallback and kind is None:
            # Typed by hand, the target's own feat, species or background beats an older creature
            # of the same name that is only a fallback ("Orc"); the creature stays reachable as
            # "Orc (creature)", and the card says so (#1913).
            newer = _asked_for(srd, typed, campaign.target_ruleset, FALLBACK_NONE)
            if newer is not None and newer.entry.kind in index.ORIGIN_KINDS:
                other = f"{hit.entry.name} (creature) {hit.tag}".strip()
                hit = newer
    if hit is None and kind is None:
        # Not a spell, a condition or a creature: it may be a Rules Glossary entry ("cover",
        # "long rest", #1891), a class feature ("rage", "rogue evasion", #1900) or a magic item
        # ("bag of holding", #1912). Those are asked for last so a spell of the same name wins.
        hit = _asked_for(srd, typed, campaign.target_ruleset, campaign.fallback_ruleset)
    # The campaign's own homebrew sits between its house rules and the book (PLAN, rules
    # precedence), and a homebrew name shadows the book's: the book's card for the same name is not
    # shown.
    if own is not None and not book_wins(
        own, hit, campaign.target_ruleset, campaign.fallback_ruleset
    ):
        own = None  # a shared book's entry ranks in its edition's place: the free rules answer
    if own is not None:
        mine = rule_card.homebrew_parts(
            own,
            campaign.name,
            player=not is_dm,
            legacy=book_is_legacy(own, campaign.target_ruleset),
        )
        if not is_dm:
            mine = rule_card.for_player(mine)
        # A followed pack's entry can be copied into the campaign's own homebrew (#1541): the
        # DM's button, never a player's.
        return Answer(HOMEBREW, mine, copy=own if own.source and is_dm else None)
    if hit is not None and hit.entry.dm_side and not is_dm:
        # The Gameplay Toolbox is the DM's chapter (#1935): a player gets the table's line, the
        # same as for a table question, and none of the chapter.
        return Answer(DM_ONLY_KIND, [DM_ONLY])
    if hit is None:
        close = srd.suggest(typed, campaign.target_ruleset, campaign.fallback_ruleset)
        text = rule_card.no_match_text(typed, rules, bool(close), player=not is_dm)
        return Answer(NONE, [text], close=list(close))
    also: list[str] = []
    if hit.entry.kind == index.FEATURE:  # the same feature of other classes, named on the card
        same = srd.lookup_all(
            hit.entry.name,
            campaign.target_ruleset,
            campaign.fallback_ruleset,
            kind=index.FEATURE,
        )
        also = [
            f"{h.entry.details['class']}{' ' + h.tag if h.tag else ''}"  # an older one is tagged
            for h in same
            if h.entry.details.get("class") != hit.entry.details.get("class")
        ]
    parts = rule_card.card_parts(
        hit, typed, rules, player=not is_dm, also=list(dict.fromkeys(also)), older=other
    )
    if not is_dm:
        parts = rule_card.for_player(parts)
    return Answer(CARD, parts)
