"""Names from a DM's own campaign background (#2167; docs/PLAN.md, "Names at scale").

The DM gives DMbot their story, notes or backstory; the AI lists the proper names in it, and each
becomes a *suggestion* in 📝 Check new names with one short quote as evidence. Nothing is confirmed
without the DM. Pure: building the request, reading and checking the answer, merging the pieces.
The AI call itself is in the caller.

The background is the DM's own writing, read once; the text is never kept. Only the names the DM
says yes to, and the quote until the name is decided, are stored. A published adventure goes the
other way (the Shared story switch, with its rights confirmation).

The answer is untrusted: a name or quote that is not in the text is dropped, so DMbot never invents
a name or a quote.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.memory.models import name_key
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.names import not_names

MAX_NAMES = 200  # suggestions from one document
NAME_WORDS = 8
NAME_CHARS = 80
QUOTE_WORDS = 20
MAX_ALSO = 4
# What the AI calls a kind, and the kind DMbot keeps it as (the same words Add many reads).
KINDS = {
    "person": "npc",  # (the AI's word for any named character; a named monster is one too)
    "place": "place",
    "group": "faction",
    "god": "npc",  # a god is an NPC (#2562)
    "item": "item",
    "event": "event",
    "idea": "concept",
}
LABELS = {  # for "Yes to all 14 places"
    "npc": ("NPC", "NPCs"),
    "place": ("place", "places"),
    "faction": ("group", "groups"),
    "item": ("item", "items"),
    "event": ("event", "events"),
    "concept": ("idea", "ideas"),
}
MAX_CONNECTIONS = 200  # suggestions from one document
# What the AI calls a connection, and the relationship DMbot keeps it as. "a <word> b" reads from
# the first name to the second ("Ulfgar" member "Frostwolf tribe").
CONNECTIONS = {
    "family": "kin_of",
    "member": "member_of",
    "in": "located_in",
    "friend": "ally_of",
    "enemy": "enemy_of",
    "owns": "owns",
    "works for": "serves",
    "knows": "knows",
}
MAX_OWN_KINDS = 20  # the campaign's own relationships offered to the AI as well
# A stated relationship no fixed kind and no campaign term fits (#2454): kept on the catch-all
# relationship, with the words the text used as its label ("pays", "sworn on the ashes of").
OTHER = "other"
WORDS_CHARS = 40


def schema(own: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The answer's shape; the campaign's own relationships are allowed kinds too."""
    return {
        "type": "object",
        "properties": {
            "names": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "also": {"type": "array", "items": {"type": "string"}},
                        "secret": {"type": "array", "items": {"type": "string"}},
                        "quote": {"type": "string"},
                    },
                    "required": ["name", "kind", "also", "secret", "quote"],
                    "additionalProperties": False,
                },
            },
            "connections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "how": {"type": "string", "enum": [*CONNECTIONS, *(own or {}), OTHER]},
                        "words": {"type": "string"},
                        "b": {"type": "string"},
                        "quote": {"type": "string"},
                    },
                    "required": ["a", "how", "words", "b", "quote"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["names", "connections"],
        "additionalProperties": False,
    }


SCHEMA = schema()


@dataclass(frozen=True, slots=True)
class Found:
    name: str
    kind: str  # the stored kind (npc, place, faction…)
    also: tuple[str, ...]
    secret: tuple[str, ...]  # disguises and secret identities: never a public name
    quote: str  # at most QUOTE_WORDS words, as written; "" when the AI's quote was not in the text


@dataclass(frozen=True, slots=True)
class Connection:
    a: str  # a name as written in the text
    how: str  # the relationship (a key of CONNECTIONS.values())
    b: str
    quote: str  # a line of the text that holds both names, as written
    secret: bool = False  # an end was found through a secret spelling: it is kept as secret only
    words: str = ""  # the label of the catch-all (`how` is then FALLBACK_PREDICATE), else ""


def cost_text(cents: int) -> str:
    """ "about 3 cents", "about $4.01": dollars once it is a dollar or more."""
    if cents >= 100:
        return f"about ${cents / 100:.2f}"
    return f"about {cents} cent{'' if cents == 1 else 's'}"


def estimate_cents(chars: int) -> int:
    """About what reading `chars` characters costs, rounded up to a whole cent and never below
    one: roughly 4 characters a token. Pass 1 (the careful model reads the text and writes a
    list up to an eighth as long) and pass 2 (the fast model reshapes the list), at the list
    prices of `ai.PRICE_PER_MILLION`, with a margin for the retries."""
    from dmbot.ai import DEFAULT_MODELS, DEFAULT_PRICE, PRICE_PER_MILLION

    careful_in, careful_out = PRICE_PER_MILLION.get(DEFAULT_MODELS.careful, DEFAULT_PRICE)
    fast_in, fast_out = PRICE_PER_MILLION.get(DEFAULT_MODELS.fast, DEFAULT_PRICE)
    tokens_in = chars / 4
    listed = max(600.0, tokens_in / 8)  # the plain list, at its fullest (a quote on every line)
    shaped = listed * 1.5  # the same list as JSON
    # The text goes in twice over: a retry or a continuation sends it again with the list so far.
    cost = tokens_in * 2 * careful_in + listed * careful_out + listed * fast_in + shaped * fast_out
    cents = cost / 1_000_000 * 100 * 1.5
    return max(1, int(cents) + (1 if cents % 1 else 0))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _json(reply: str) -> object:
    text = reply.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                return None
    return None


def _has(haystack: str, text: str) -> bool:
    """Is `text` in the (squashed) haystack as whole words? "Ann" is not in "annual"."""
    return re.search(rf"(?<!\w){re.escape(_squash(text))}(?!\w)", haystack) is not None


def _spellings(
    raw: object, name: str, haystack: str, *, skip: set[str], cued: frozenset[str]
) -> list[str]:
    """The other spellings in one list the text really holds, each once, not the name itself."""
    out: list[str] = []
    for other in raw if isinstance(raw, list) else []:
        if not isinstance(other, str):
            continue
        other = " ".join(other.split())
        key = name_key(other)
        if (
            other
            and len(other) <= NAME_CHARS
            and "|" not in other
            and key != name_key(name)
            and key not in skip
            and _has(haystack, other)
            and not_names.allowed(other, cued)
        ):
            skip.add(key)
            out.append(other)
    return out


def read_answer(reply: str, piece: str) -> list[Found]:
    """The names in one answer that are really in `piece`. A name, other spelling or quote the
    text does not hold (as whole words) is dropped (a quote alone: the name stays, without it)."""
    data = _json(reply)
    items = data.get("names") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    haystack = _squash(piece)
    cued = not_names.cued_in(piece)  # a pronoun is a name only where the text says so (#2358)
    out: list[Found] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        kind = KINDS.get(str(item.get("kind", "")).strip().lower())
        if not isinstance(name, str) or kind is None:
            continue
        name = " ".join(name.split())
        if (
            not name
            or len(name) > NAME_CHARS
            or len(name.split()) > NAME_WORDS
            or "|" in name
            or not _has(haystack, name)
            or not not_names.allowed(name, cued)
        ):
            continue
        # A disguise is read first, so the same spelling is never also a public one.
        seen: set[str] = set()
        secret = _spellings(item.get("secret"), name, haystack, skip=seen, cued=cued)
        also = _spellings(item.get("also"), name, haystack, skip=seen, cued=cued)
        quote = item.get("quote")
        kept = ""
        if isinstance(quote, str):
            words = quote.split()
            if words and len(words) <= QUOTE_WORDS and _has(haystack, quote):
                kept = " ".join(words)
        out.append(Found(name, kind, tuple(also[:MAX_ALSO]), tuple(secret[:MAX_ALSO]), kept))
    return out


def in_the_rules(name: str, kind: str, target: str, fallback: str) -> bool:
    """Is this a kind of monster, or a spell, in the rules? Those belong to the rules, not to the
    campaign (a proper name like "Snot" is not a monster). A person counts as a monster when the
    rules hold that name ("Owlbear"). A spell name is only looked for among ideas and items: a
    place called "Sanctuary" or a person called "Wish" is a real name."""
    from dmbot.rules import index  # late: the rules data loads on first use

    rules = index.srd()
    if kind == "npc" and rules.is_monster(name):
        return True
    if kind in ("concept", "item"):
        return rules.lookup(name, target, fallback, kind="spell") is not None
    return False


def merge(
    answers: Iterable[list[Found]],
    skip_keys: Collection[str] = (),
    *,
    cap: int = MAX_NAMES,
    rules: tuple[str, str] | None = None,
) -> list[Found]:
    """The pieces' names as one list, each once (other spellings joined, the first quote kept),
    leaving out names DMbot already has, ordinary words and game terms, at most `cap`. A name
    that is a disguise of another entry folds into it, and its other spellings become that
    entry's secret spellings. `rules`
    (target, fallback ruleset): names the rules hold as a monster or spell are left out too."""
    skip = {name_key(k) for k in skip_keys} | COMMON | GAME_TERMS
    order: list[str] = []
    merged: dict[str, Found] = {}
    for found in answers:
        for item in found:
            key = name_key(item.name)
            if not key or key in skip:
                continue
            if rules is not None and in_the_rules(item.name, item.kind, *rules):
                continue
            item = Found(  # a spelling DMbot already holds (a secret one too) is not offered again
                item.name,
                item.kind,
                tuple(a for a in item.also if name_key(a) not in skip),
                tuple(a for a in item.secret if name_key(a) not in skip),
                item.quote,
            )
            old = merged.get(key)
            if old is None:
                order.append(key)
                merged[key] = item
                continue
            seen = {name_key(x) for x in (*old.also, *old.secret)}
            more = tuple(x for x in item.also if name_key(x) not in seen)
            more_secret = tuple(x for x in item.secret if name_key(x) not in seen)
            merged[key] = Found(
                old.name,
                old.kind,
                (*old.also, *more)[:MAX_ALSO],
                (*old.secret, *more_secret)[:MAX_ALSO],
                old.quote or item.quote,
            )
    out = [merged[k] for k in order]
    # A disguise is a secret spelling of the real one. When the AI also lists the disguise as a
    # name of its own (as it is told to), that entry folds into the real one's secret spelling:
    # never a second, public entry. An entry folds only into one that is itself kept: of two that
    # hold each other, the first stays, and a chain folds from the top.
    holder: dict[str, str] = {}
    for f in out:
        for a in f.secret:
            holder.setdefault(name_key(a), name_key(f.name))
    place = {name_key(f.name): i for i, f in enumerate(out)}
    # Walk down from the entries nobody holds. An entry whose holder is kept folds into it; one
    # whose holder was folded stays (its holder is not there to keep it), and so on down. A loop
    # with no top (each holds the next) keeps its earliest entry and walks from there, so every
    # name is either an entry or a kept entry's secret. (In a chain, a folded entry's own secret
    # becomes a public entry rather than moving under the top one: allowed, nothing is lost.)
    children: dict[str, list[str]] = {}
    for key, above in holder.items():
        if above != key and above in place and key in place:
            children.setdefault(above, []).append(key)
    kept_keys: dict[str, bool] = {}

    def settle(start: str, keep: bool) -> None:
        stack = [(start, keep)]
        while stack:
            key, is_kept = stack.pop()
            if key in kept_keys:
                continue
            kept_keys[key] = is_kept
            for child in children.get(key, ()):
                stack.append((child, not is_kept))

    for key in place:
        if holder.get(key) is None or holder[key] == key or holder[key] not in place:
            settle(key, True)  # nobody holds it
    for key in place:  # what is left hangs in a loop: the earliest stays
        settle(key, True)
    # The cap counts the kept entries only: a folded disguise takes no slot, so it can never
    # cost its holder one. A holder that is cut takes its folded disguise's spellings with it.
    folded = [f for f in out if not kept_keys[name_key(f.name)]]
    out = [f for f in out if kept_keys[name_key(f.name)]][:cap]
    # A folded entry's other spellings (its secret ones too) name the same disguise, so they join
    # its holder as secret spellings, never public ones and never another entry's own name:
    # "Brother Aldric also [Aldric]" folded into Vecnor leaves Vecnor holding "Aldric" in secret.
    # A loop is allowed to leave a kept entry as another kept entry's secret too (Vecnor is an
    # entry and Mira's secret): every name is then somewhere, and nothing is lost to the fold.
    extra: dict[str, list[str]] = {}
    for gone in folded:
        top = holder.get(name_key(gone.name))
        if top is not None:
            extra.setdefault(top, []).extend((*gone.also, *gone.secret))
    joined: list[Found] = []
    entry_names = {name_key(f.name) for f in out}  # another entry's own name stays that entry's
    for f in out:
        have = {name_key(x) for x in (f.name, *f.also, *f.secret)} | entry_names
        carried: list[str] = []
        for a in extra.get(name_key(f.name), ()):
            if name_key(a) not in have and name_key(a) not in skip:
                have.add(name_key(a))
                carried.append(a)
        joined.append(
            Found(f.name, f.kind, f.also, (*f.secret, *carried)[:MAX_ALSO], f.quote)
            if carried
            else f
        )
    out = joined
    # An other spelling that is another name's own name is that name's, not an alias; and a
    # spelling a name holds as secret is never also public.
    names = {name_key(f.name) for f in out}
    out = [
        Found(
            f.name,
            f.kind,
            tuple(
                a
                for a in f.also
                if name_key(a) not in names and name_key(a) not in {name_key(x) for x in f.secret}
            ),
            f.secret,
            f.quote,
        )
        for f in out
    ]
    return out


def counts_by_kind(found: Iterable[Found]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in found:
        out[item.kind] = out.get(item.kind, 0) + 1
    return out


def read_connections(reply: str, piece: str, own: Collection[str] = ()) -> list[Connection]:
    """The connections in one answer that the text really holds: both names and the quote are in
    `piece` as whole words and the quote holds both names, so DMbot never invents a link. The
    names are checked against the merged list later (`keep_connections`)."""
    data = _json(reply)
    items = data.get("connections") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    haystack = _squash(piece)
    out: list[Connection] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        a, b, quote = item.get("a"), item.get("b"), item.get("quote")
        word = str(item.get("how", "")).strip().lower()
        how = CONNECTIONS.get(word) or next((k for k in own if k.lower() == word), None)
        label = ""
        if word == OTHER:  # the catch-all, kept with the words the text used
            label = " ".join(str(item.get("words") or "").split())
            if not label or len(label) > WORDS_CHARS or "|" in label:
                continue
            # The label is the one free text of a connection: its words must be the quote's own,
            # so nothing carried in from the document or the list can ride in as a label.
            quoted = set(re.findall(r"\w+", _squash(str(quote))))
            if not set(re.findall(r"\w+", _squash(label))) <= quoted:
                continue
            how = FALLBACK_PREDICATE
        if how is None or not all(isinstance(x, str) for x in (a, b, quote)):
            continue
        a, b = " ".join(str(a).split()), " ".join(str(b).split())
        words = str(quote).split()
        if (
            not a
            or not b
            or len(a) > NAME_CHARS
            or len(b) > NAME_CHARS
            or name_key(a) == name_key(b)
            or not words
            or len(words) > QUOTE_WORDS
        ):
            continue
        said = " ".join(words)
        if (
            _has(haystack, a)
            and _has(haystack, b)
            and _has(_squash(said), a)
            and _has(_squash(said), b)
            and _has(haystack, said)
        ):
            out.append(Connection(a, how, b, said, words=label))
    return out


def keep_connections(
    answers: Iterable[list[Connection]],
    names: Iterable[Found],
    known: Mapping[str, str],
    *,
    cap: int = MAX_CONNECTIONS,
    skip: Callable[[Connection], bool] | None = None,
) -> list[Connection]:
    """The pieces' connections as one list, each once, whose two ends are names DMbot will have:
    one of this reading's names (or its other spelling, public or secret) or `known` (a name key
    of the campaign's own mapped to its name). Each end becomes the name it stands for. The same
    pair is offered once, whichever way round or how it was said (one fact per pair and kind;
    friends and family read the same both ways). At most `cap`."""
    names = list(names)
    ends: dict[str, str] = dict(known)
    for f in names:  # every public name first: another name's spelling never takes it over
        ends.setdefault(name_key(f.name), f.name)
    hidden = {name_key(x) for f in names for x in f.secret} - {name_key(f.name) for f in names}
    claimed: dict[str, set[str]] = {}
    for f in names:
        for spelling in (*f.also, *f.secret):
            claimed.setdefault(name_key(spelling), set()).add(f.name)
    for key, owners in claimed.items():
        # a spelling that two names claim is no end: DMbot would be guessing which one is meant
        if key not in ends and len(owners) == 1:
            ends[key] = next(iter(owners))
    both_ways = {"kin_of", "ally_of", "enemy_of"}
    seen: set[tuple[str, str, str]] = set()
    out: list[Connection] = []
    for piece in answers:
        for c in piece:
            a, b = ends.get(name_key(c.a)), ends.get(name_key(c.b))
            if a is None or b is None or name_key(a) == name_key(b):
                continue
            ka, kb = name_key(a), name_key(b)
            if c.how in both_ways and kb < ka:
                ka, kb = kb, ka
            if (ka, c.how, kb) in seen:  # (one catch-all for a pair, whatever its words)
                continue
            seen.add((ka, c.how, kb))
            if skip is not None and skip(Connection(a, c.how, b, c.quote, words=c.words)):
                continue  # the campaign has it already, or an end is no name it has
            out.append(
                Connection(
                    a,
                    c.how,
                    b,
                    c.quote,
                    name_key(c.a) in hidden or name_key(c.b) in hidden,
                    c.words,
                )
            )
            if len(out) >= cap:
                return out
    return out
