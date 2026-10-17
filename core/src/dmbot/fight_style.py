"""How the table runs initiative, heard (#1771; docs/PLAN.md, "FightBot", "Initiative style,
heard"). No AI: a small, strict phrase list in the manner of `timebot/combat_phrases.py`, and a
tiny state that keeps what was heard. It changes only *when* FightBot speaks, never the combat
state, the round or anything a player sees.

Two separate things are heard. The order: `each` (one creature at a time), `side` (each side acts
together), `reroll` (initiative is rolled again every round), else `unknown`. Whether the table
declares what it will do before a round plays out: `yes`, `no` or `unknown`.

Whole short lines only, and never a question, a wish, a condition or a story ("he'll roll
initiative when the door opens"). One exception is on purpose: "what's everyone doing this
round?" is a question, but it is the DM asking for declarations.

Who counts: a DM's line sets the style. A player's lines count only while no DM line has said
otherwise, and it takes two lines from two different players who still agree to be recorded. A
player's words never move a turn or the round.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from dmbot.timebot.combat_phrases import _LEAD, _NOT_A_THING, _NUMBER, _ONES, MAX_WORDS
from dmbot.timebot.phrases import normalise

Order = Literal["unknown", "each", "side", "reroll"]
Declare = Literal["unknown", "yes", "no"]

PLAYER_SIDE = frozenset({"players", "party", "heroes", "adventurers", "your side", "the party"})
VOTES_NEEDED = 2  # lines from different players, when no DM has said


@dataclass(frozen=True, slots=True)
class Heard:
    """What one line says about initiative (all empty: nothing)."""

    order: Order | None = None  # the order style it points to
    declarations: Declare | None = None
    turn: tuple[int, ...] = ()  # players whose turn was called ("Mira, you're up")
    on_deck: tuple[int, ...] = ()  # players called next ("Kael, you're next")
    creature: str | None = None  # a monster's turn called ("goblin two, its turn")
    players_side: bool = False  # the players' side was called to act
    declare_call: bool = False  # the DM asked what everyone will do
    reroll_call: bool = False  # initiative called again in an ongoing fight
    rolled: bool = False  # a player said their initiative roll (each or reroll, never side)
    # The order was only guessed from a turn being called ("Mira, you're up"). That can be
    # narration, so it never overrules a style the DM stated ("side initiative").
    guess: bool = False


_DECLARE = re.compile(
    rf"{_LEAD}(?:declare (?:your )?(?:actions|intentions|moves)"
    r"|(?:everyone|everybody)[, ]+(?:say|tell me) what (?:you'?re|you are) doing"
    r"|what(?:'?s| is) (?:everyone|everybody) doing(?: this round)?"
    r"|declarations)[.!]?$",
    re.IGNORECASE,
)
_ROLL = re.compile(
    rf"{_LEAD}(?:(?:re-?roll|roll) initiative again|re-?roll initiative"
    r"|everyone roll again|(?:we )?(?:re-?roll|roll) initiative (?:every|each) round)[.!]?$",
    re.IGNORECASE,
)
_ROLL_START = re.compile(rf"{_LEAD}(?:roll|make) (?:for )?initiative[.!]?$", re.IGNORECASE)
_SIDE_STYLE = re.compile(
    rf"{_LEAD}(?:(?:(?:we )?(?:use|do|run) )?(?:side|group)(?:[- ]based)? initiative"
    r"|initiative by (?:side|group)s?)[.!]?$",
    re.IGNORECASE,
)
_SIDE_ACTS = re.compile(
    rf"{_LEAD}(?:the )?(?P<who>players|party|heroes|adventurers|monsters|enemies|foes|bad guys"
    r"|your side|the other side)(?: go(?:es)?| act(?:s)?)(?: first| now| next)?[.!]?$",
    re.IGNORECASE,
)
_PLURAL_TURN = re.compile(
    rf"{_LEAD}(?:the )?(?P<who>[a-z][a-z ]*s)' turn[.!]?$",
    re.IGNORECASE,
)
_NAME = r"[a-z][a-z0-9'\-]*(?: [a-z0-9][a-z0-9'\-]*){0,2}"
_UP = re.compile(
    rf"{_LEAD}(?P<name>{_NAME}),? (?:you'?re up|you are up|it'?s your turn|your turn)[.!]?$",
    re.IGNORECASE,
)
_NEXT = re.compile(
    rf"{_LEAD}(?P<name>{_NAME}),? (?:you'?re (?:on deck|next)|you are (?:on deck|next))[.!]?$",
    re.IGNORECASE,
)
_CREATURE = re.compile(
    rf"{_LEAD}(?:the )?(?P<mon>[a-z][a-z\-]*(?: [a-z\-]+)??)(?: (?P<n>{_NUMBER}))?"
    r"(?:,? (?:its|it'?s|his|her) turn)[.!]?$",
    re.IGNORECASE,
)
_POSSESSIVE = re.compile(rf"{_LEAD}(?P<name>{_NAME})'s turn[.!]?$", re.IGNORECASE)
_TEENS = "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen"
_UNITS = "one|two|three|four|five|six|seven|eight|nine"
_SAID_NUMBER = rf"\d{{1,2}}|{_TEENS}|(?:twenty|thirty|forty)(?:[- ](?:{_UNITS}))?|{_UNITS}"  # "17"
_ROLLED = re.compile(
    rf"^(?:i (?:got|rolled|have) (?:a |an )?(?P<a>{_SAID_NUMBER})"
    rf"|(?P<b>{_SAID_NUMBER}) (?:for|on) initiative)[.!]?$",
    re.IGNORECASE,
)
_NOT_NAMES = frozenset(
    {"the", "a", "an", "you", "it", "it's", "its", "he", "she", "they", "we", "i", "everyone",
     "that", "this", "there", "now", "okay", "ok", "so", "and", "well", "right", "sure",
     "yeah", "yes", "hey", "oh", "hmm"}
)  # fmt: skip


def _number(word: str) -> int:
    return int(word) if word.isdigit() else _ONES[word.lower()]


MAX_CHARS = 200  # a spoken order is short; long lines never reach the patterns


def _guarded(text: str) -> bool:
    return (
        not text or "?" in text or len(text.split()) > MAX_WORDS or bool(_NOT_A_THING.search(text))
    )


def hear(line: str, players: Mapping[str, int], *, round_number: int | None = None) -> Heard | None:
    """What a line says about how initiative is run, or None. `players` maps a lower-case name
    (a character's, or a person's name at the table) to that person's Discord id. `round_number`
    is the fight's round when known: "roll initiative" again at round 2 or later is a reroll."""
    text = normalise(line).strip()
    if len(text) > MAX_CHARS:
        return None
    plain = text.rstrip("?!. ")
    if _DECLARE.match(plain):  # a question on purpose: checked before the guard
        return Heard(declarations="yes", declare_call=True)
    if _guarded(text):
        return None
    low = text.lower()
    if _ROLL.match(low):
        return Heard(order="reroll", reroll_call=True)
    if round_number is not None and round_number >= 2 and _ROLL_START.match(low):
        return Heard(order="reroll", reroll_call=True)
    if _SIDE_STYLE.match(low):
        return Heard(order="side")
    found = _SIDE_ACTS.match(low)
    if found is not None:
        who = found.group("who").lower()
        return Heard(order="side", players_side=who in PLAYER_SIDE)
    plural = _PLURAL_TURN.match(low)
    if plural is not None:
        who = plural.group("who").strip()
        user = players.get(who)  # "Chris' turn" is a person whose name ends in s
        if user is not None:
            return Heard(order="each", turn=(user,), guess=True)
        return Heard(order="side", players_side=who in PLAYER_SIDE)
    if _ROLLED.match(low):
        return Heard(rolled=True)
    found = _NEXT.match(low)
    if found is not None:
        user = players.get(found.group("name").lower())
        if user is None and found.group("name").lower() in _NOT_NAMES:
            return None
        return Heard(order="each", on_deck=() if user is None else (user,), guess=True)
    found = _UP.match(low)
    if found is not None:
        user = players.get(found.group("name").lower())
        if user is None and found.group("name").lower() in _NOT_NAMES:
            return None
        return Heard(order="each", turn=() if user is None else (user,), guess=True)
    found = _CREATURE.match(low)
    if found is not None:
        mon = found.group("mon").lower().strip()
        user = players.get(mon)
        if user is not None:
            return Heard(order="each", turn=(user,), guess=True)
        if mon in _NOT_NAMES or mon.endswith("s"):  # "the goblins" is a side, not one creature
            return None
        number = found.group("n")
        key = mon if number is None else f"{mon} {_number(number)}"
        return Heard(order="each", creature=key, guess=True)
    found = _POSSESSIVE.match(low)
    if found is not None:  # "Mira's turn": a person at the table; "the dragon's turn" is narration
        user = players.get(found.group("name").lower())
        return None if user is None else Heard(order="each", turn=(user,), guess=True)
    return None


_ORDER_WORDS = {"each": "one at a time", "side": "by side", "reroll": "rolled each round"}
SAVED_STYLES = frozenset(
    {"each", "side", "reroll", "declare", "each+declare", "side+declare", "reroll+declare"}
)  # what a campaign keeps between sessions (#1784); the same list is a CHECK in the database


def decode(saved: str | None) -> tuple[Order, Declare]:
    """The order and declarations a kept style stands for (anything unknown: nothing)."""
    if saved not in SAVED_STYLES:
        return "unknown", "unknown"
    parts = (saved or "").split("+")
    order: Order = "unknown"
    if "each" in parts:
        order = "each"
    elif "side" in parts:
        order = "side"
    elif "reroll" in parts:
        order = "reroll"
    return order, "yes" if "declare" in parts else "unknown"


def encode(order: str, declarations: str) -> str | None:
    """The kept form. "No declarations" is not kept (it is only ever FightBot's own inference),
    so an order of unknown with declarations of "no" keeps nothing."""
    parts = ([order] if order in _ORDER_WORDS else []) + (
        ["declare"] if declarations == "yes" else []
    )
    return "+".join(parts) or None


@dataclass(slots=True)
class StyleState:
    """What this session has heard about how the table runs initiative."""

    order: Order = "unknown"
    declarations: Declare = "unknown"
    # Who set each: "dm" (a DM said it), "dm_guess" (a DM's turn call suggested it: a stated
    # style outranks it), "players" (two of them agreed), "inferred" (a round played out with
    # turns and no call for declarations).
    set_by: dict[str, str] = field(default_factory=dict)
    votes: dict[tuple[str, str], set[int]] = field(default_factory=dict)

    def vote(
        self,
        dimension: str,
        value: str,
        speaker: int,
        *,
        from_dm: bool,
        consenting: Callable[[int], bool],
        guessed: bool = False,
    ) -> bool:
        """Count one line for `value` in `dimension` ("order" or "declarations"). `guessed`: the
        value is only suggested by a turn being called. True if the heard style changed."""
        if from_dm:
            self.votes.pop((dimension, value), None)
            if guessed:
                if self.set_by.get(dimension) in ("dm", "saved"):
                    return False  # a stated or kept style: a guess never overrules it
                return self._set(dimension, value, "dm_guess")
            return self._set(dimension, value, "dm")
        if self.set_by.get(dimension, "").startswith("dm"):
            return False  # a DM has said: players' lines no longer count
        voters = {v for v in self.votes.setdefault((dimension, value), set()) if consenting(v)}
        if consenting(speaker):
            voters.add(speaker)
        self.votes[(dimension, value)] = voters
        if len(voters) >= VOTES_NEEDED:
            return self._set(dimension, value, "players")
        return False

    @classmethod
    def from_saved(cls, saved: str | None) -> StyleState:
        """A session's starting point: the style the campaign kept last time (#1784), marked as
        such. A DM's line overrides it; players still need two agreeing."""
        order, declarations = decode(saved)
        state = cls(order=order, declarations=declarations)
        if order != "unknown":
            state.set_by["order"] = "saved"
        if declarations != "unknown":
            state.set_by["declarations"] = "saved"
        return state

    def to_keep(self, kept: str | None) -> str | None:
        """What the campaign should keep: a dimension a DM's words set this session (stated, or
        suggested by the DM calling turns, since a table that runs "one at a time" is otherwise
        never heard to say so) is kept as it now is; every other stays as it was kept. A players'
        vote and the "no declarations" FightBot works out for itself are never kept."""
        order, declarations = decode(kept)
        if self.set_by.get("order", "").startswith("dm"):
            order = self.order
        if self.set_by.get("declarations", "").startswith("dm"):
            declarations = self.declarations
        return encode(order, declarations)

    def infer_no_declarations(self) -> bool:
        if self.declarations != "unknown":
            return False
        return self._set("declarations", "no", "inferred")

    def forget_inferred(self) -> bool:
        """A fight ended: what was only inferred for it is forgotten."""
        if self.set_by.get("declarations") != "inferred":
            return False
        self.declarations, changed = "unknown", True
        self.set_by.pop("declarations", None)
        return changed

    def prune(self, consenting: Callable[[int], bool]) -> bool:
        """Drop the lines of anyone who no longer agrees to be recorded; a style that only they
        made is forgotten. True if the heard style changed."""
        changed = False
        for key, voters in list(self.votes.items()):
            self.votes[key] = {v for v in voters if consenting(v)}
        for dimension in ("order", "declarations"):
            if self.set_by.get(dimension) != "players":
                continue
            value = getattr(self, dimension)
            if len(self.votes.get((dimension, value), ())) < VOTES_NEEDED:
                setattr(self, dimension, "unknown")
                self.set_by.pop(dimension, None)
                changed = True
        return changed

    def _set(self, dimension: str, value: str, by: str) -> bool:
        if getattr(self, dimension) == value and self.set_by.get(dimension) == by:
            return False
        was = getattr(self, dimension)
        setattr(self, dimension, value)
        self.set_by[dimension] = by
        return bool(was != value)

    @property
    def known(self) -> bool:
        """Is there a style to time reminders to? Otherwise FightBot works as it always did."""
        return bool(self.order != "unknown" or self.declarations == "yes")

    def dm_phrase(self) -> str:
        """The one plain phrase for the DM's combat line."""
        parts = []
        if self.order != "unknown":
            parts.append(_ORDER_WORDS[self.order])
        if self.declarations == "yes":
            parts.append("actions declared first")
        # Until something is heard this session, what shows is what was kept from last time.
        last_time = bool(self.set_by) and all(by == "saved" for by in self.set_by.values())
        head = "Initiative, as heard last time: " if last_time else "Initiative, as heard: "
        return head + ("; ".join(parts) if parts else "not heard yet")
