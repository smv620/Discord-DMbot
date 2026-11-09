"""A connection between two names, in plain words (the fixed core of the memory rules). One place
for the Discord name card and the website's relationship web (#2323), so they say the same thing.
Pure: no Discord, no database."""

from __future__ import annotations

import re

from dmbot.memory import kin, place_words, reputation_words
from dmbot.memory.ontology import CORE_PREDICATES, DISGUISED_AS, FALLBACK_PREDICATE, SYNONYMS

CONNECTION_WORDS = {
    "located_in": "is in",
    "member_of": "is a member of",
    "ally_of": "is a friend or ally of",
    "enemy_of": "is an enemy of",
    "kin_of": "is family of",
    "owns": "owns",
    "knows": "knows",
    "serves": "works for",
    "sworn_to": "is sworn to",
    "carries": "carries",
    "rival_of": "is a rival of",
    "mentor_of": "teaches",
    "leads": "leads",
    "founded": "founded",
    "travels_with": "travels with",
    "captive_of": "is held captive by",
    "contains": "contains",
    "disguised_as": "is disguised as",
}
# The same connection read from the other name's card ("Frostwolf tribe has as members
# Ulfgar"). Connections that read the same both ways use CONNECTION_WORDS.
CONNECTION_BACK = {
    "located_in": "is linked to",  # (the menu has no kind to go by: it claims nothing)
    "member_of": "has as a member",
    "owns": "is owned by",
    "knows": "is known to",
    "serves": "is the boss of",
    "sworn_to": "is owed an oath by",
    "carries": "is carried by",
    "mentor_of": "is taught by",
    "leads": "is led by",
    "founded": "was founded by",
    "travels_with": "is joined by",
    "captive_of": "holds captive",
    "contains": "is inside",
    "disguised_as": "is impersonated by",
}
# How two people know each other (#3881), on a `knows`: the words for "met", "aware" and "" (the
# story doesn't say). Shown only where the caller passes the row's `knows_how`.
KNOWS_HOW_WORDS = {"met": "has met", "aware": "is aware of", "": "knows of"}
# The same, read from the other name's card. Two people who met have both met; one who is only
# heard of is "known of by" the other.
KNOWS_HOW_BACK = {"met": "has met", "aware": "is known of by", "": "is known to"}
# On a card, where several names may follow ("members include Ulfgar, Kesh").
CARD_BACK = {**CONNECTION_BACK, "member_of": "members include"}
# The old friend and enemy connections are the same thing as a `knows` with a feeling: how a name
# feels about another is the DM's Reputation, so a non-DM reads plain "knows" (#3611, #3610).
OLD_FEELINGS = frozenset({"ally_of", "enemy_of"})
BOTH_WAYS = frozenset({"ally_of", "enemy_of", "rival_of", "kin_of"})


def for_reader(predicate: str, detail: str, *, feeling: bool) -> tuple[str, str]:
    """The connection a reader is shown: for anyone but the DM, a `knows` with a feeling or an old
    friend/enemy connection is a plain `knows` with no words (#3610, #3611)."""
    if feeling:
        return predicate, detail
    if predicate in OLD_FEELINGS or (
        predicate == "knows" and reputation_words.word_of(detail) in reputation_words.WORDS
    ):
        return "knows", ""
    return predicate, detail


def connection_words(predicate: str) -> str:
    return CONNECTION_WORDS.get(predicate, predicate.replace("_", " "))


KIN = "kin_of"
CORE_KEYS = frozenset(p.key for p in CORE_PREDICATES)


def reads_both_ways(predicate: str, detail: str = "", directional: bool = False) -> bool:
    """Does the connection read the same from either name? Not a family connection whose role
    the DM gave a direction: "niece" is true of one name only (#2457)."""
    return predicate in BOTH_WAYS and not (
        predicate == KIN and directional and kin.directional(detail)
    )


_KNOWS_FEELING = re.compile(r"knows\s*\(\s*([^()]+?)\s*\)", re.IGNORECASE)


def predicate_for(words: str) -> tuple[str, str]:
    """(predicate, detail) for connection words a DM typed or picked on the website: the words of a
    fixed connection ("is a member of") give that connection; family words ("is the niece of") give
    family with that role; anything else is the catch-all with the words as its label, exactly as a
    connection said at the table is kept (#1303). Family words that read from the second name's
    side ("has a niece") are not taken here: see `predicate_for_either`."""
    clean = " ".join(words.split())
    for predicate, known in CONNECTION_WORDS.items():
        if clean.casefold() == known.casefold():
            return predicate, ""
    parsed = kin.parse(clean)
    if parsed is not None and not parsed[1]:
        return KIN, parsed[0]
    if SYNONYMS.get("_".join(clean.casefold().split())) == DISGUISED_AS:
        return DISGUISED_AS, ""  # "is posing as" and the like (#3091): never the public catch-all
    # "knows (wary)": the words `link_words` writes for a feeling read back as that feeling, not as
    # a catch-all labelled with them (#3606). A word outside the nine stays the catch-all.
    said = _KNOWS_FEELING.fullmatch(clean)
    if said is not None and (word := reputation_words.word_of(said.group(1))) is not None:
        return "knows", ("" if word == reputation_words.NEUTRAL else word)  # (neutral stores none)
    return FALLBACK_PREDICATE, clean


def predicate_for_either(words: str) -> tuple[str, str, bool]:
    """Like `predicate_for`, and also takes family words read from the second name's side
    ("has a niece"): the third value says the two names must be swapped before storing."""
    parsed = kin.parse(" ".join(words.split()))
    if parsed is not None and parsed[1]:
        return KIN, parsed[0], True
    predicate, detail = predicate_for(words)
    return predicate, detail, False


def link_words(
    predicate: str,
    detail: str,
    directional: bool = False,
    subject_kind: str = "",
    *,
    feeling: bool = False,
    knows_how: str | None = None,
) -> str:
    """How one connection reads from its first name to its second. On the catch-all, the words it
    was said in ("sworn on the ashes of") are the connection's own label (#1303); on family, the
    role ("is the niece of"). A place tie reads by its word ("lives in"), and for something that
    moves with no word, "was last seen in", never "is in" (#3363); `subject_kind` is the first
    name's kind ("" when the caller does not know it, which keeps the old "is in").

    `feeling` says the reader is the DM: how a name feels about another is the DM's Reputation, so
    "knows (hostile)" is worded only for a DM-only screen, and everyone else reads plain "knows"
    (#3610). `knows_how` (#3881): the row's word on whether they've met, so a `knows` reads "has
    met", "is aware of" or "knows of"; None where the caller has no row (a menu of kinds), which
    keeps plain "knows"."""
    predicate, detail = for_reader(predicate, detail, feeling=feeling)
    knows = (
        KNOWS_HOW_WORDS.get(knows_how, "knows of")
        if predicate == "knows" and knows_how is not None
        else "knows"
    )
    if predicate == "located_in":
        return place_words.forward(detail, subject_kind)
    if predicate == FALLBACK_PREDICATE and detail:
        return detail
    # "knows" with the feeling a reader gave it (#3175): "knows (hostile)", the old friend and enemy
    if (
        feeling
        and predicate == "knows"
        and reputation_words.word_of(detail) in reputation_words.WORDS
    ):
        return f"{knows} ({detail})"
    # A role true of both names (sibling, cousin, spouse) reads the same from either card, so it is
    # shown whatever its stored direction is (#3102). A one-way role is shown only when directional.
    if predicate == KIN and (role := kin.role_of(detail)) and (directional or role in kin.MUTUAL):
        return kin.forward(role)
    if predicate == "knows":
        return knows
    return connection_words(predicate)


def own_words(predicate: str, detail: str) -> bool:
    """True for a connection that has no kind of its own (the catch-all) and carries the table's
    own words: the website shows those in quotes, so they are not taken for DMbot's own wording
    (#3574)."""
    return predicate == FALLBACK_PREDICATE and bool(detail)


def quoted(detail: str) -> str:
    """The table's own words in curly quotes, for display only (the stored words stay raw)."""
    return f"\u201c{detail}\u201d"


def back_after(
    predicate: str, detail: str, directional: bool = False, subject_kind: str = ""
) -> str:
    """The words that follow the name when a connection reads from its second name ("was last
    seen" in "Aldmoor is where Tavin was last seen"); empty for every other connection."""
    return place_words.backward_after(detail, subject_kind) if predicate == "located_in" else ""


def campaign_back(predicate: str) -> str | None:
    """The back words of a kind this campaign made for itself (#3033): no reverse words are
    invented, so it reads "is linked (“sworn on the ashes of”) to". None for any core kind."""
    if predicate in CORE_KEYS:
        return None
    return f"is linked (“{connection_words(predicate)}”) to"


def back_words(
    predicate: str, detail: str, directional: bool = False, subject_kind: str = ""
) -> str | None:
    """How a connection reads from its second name, when that differs from `link_words`: "has a
    niece". None for one that reads the same either way, or has no fixed reading back."""
    if predicate == "located_in":
        return place_words.backward(detail, subject_kind)
    if predicate == KIN and directional and kin.directional(detail):
        return kin.backward(kin.role_of(detail) or "")
    return CONNECTION_BACK.get(predicate) or campaign_back(predicate)
