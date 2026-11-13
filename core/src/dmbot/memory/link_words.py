"""A connection between two names, in plain words (the fixed core of the memory rules). One place
for the Discord name card and the website's relationship web (#2323), so they say the same thing.
Pure: no Discord, no database."""

from __future__ import annotations

import re

from dmbot.memory import kin, next_words, place_words, reputation_words, route_words
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
    "next_to": next_words.PLAIN,  # (its word, when it has one, reads instead: `next_words`)
    "route_to": route_words.OTHER_FORWARD,  # (its means reads instead: `route_words`, #4173 6d)
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
    # (a one-way "on the shore of", read from the lake's card: still true and plain, #4173 6a)
    "next_to": next_words.PLAIN,
    "route_to": route_words.OTHER_FORWARD,  # (a one-way route read from where it leads)
}
# How two people know each other (#3881), on a `knows`: the words for "met", "aware" and "" (the
# story doesn't say). Shown only where the caller passes the row's `knows_how`.
# "Knows" stays the words when the story doesn't say (the ux-critic on #3881: "knows of" already
# means "only heard of" in plain English, and would claim they never met).
KNOWS_HOW_WORDS = {"met": "has met", "aware": "has heard of", "": "knows"}
# The same, read from the other name's card. Two people who met have both met; one who is only
# heard of is still "known to" the other, which is true and plain.
KNOWS_HOW_BACK = {"met": "has met", "aware": "is known to", "": "is known to"}
# On a card, where several names may follow ("members include Ulfgar, Kesh").
CARD_BACK = {**CONNECTION_BACK, "member_of": "members include"}
# The old friend and enemy connections are the same thing as a `knows` with a feeling: how a name
# feels about another is the DM's Reputation, so a non-DM reads plain "knows" (#3611, #3610).
OLD_FEELINGS = frozenset({"ally_of", "enemy_of"})
BOTH_WAYS = frozenset({"ally_of", "enemy_of", "rival_of", "kin_of", "next_to", "route_to"})
# How a two-way connection reads with both names first ("Cerric and Wit are rivals", #4154): the
# one table Discord and the website take it from. A kind not here, made two-way by the DM, reads
# "are connected (“<its one-way words>”)" (`pair_words`), never "A and B: is sworn to".
PAIR_WORDS = {
    "rival_of": "are rivals",
    "ally_of": "are friends or allies",
    "enemy_of": "are enemies",
    "kin_of": "are family",
    "knows": "know each other",
    "travels_with": "travel together",
    "next_to": next_words.PLAIN_PAIR,
    "route_to": route_words.OTHER_PAIR,
    FALLBACK_PREDICATE: "are connected",
}
# A kind that reads both ways by default, made one-way by the DM (#4154), read from its second
# name: "Wit is seen as a rival by Cerric". Family with no role is family from either side.
ONE_WAY_BACK = {
    "rival_of": "is seen as a rival by",
    "ally_of": "is seen as a friend or ally by",
    "enemy_of": "is seen as an enemy by",
    "kin_of": "is family of",
}
# A role true of both names, said of the two together ("are cousins")
KIN_PAIR_WORDS = {"sibling": "are siblings", "spouse": "are married", "cousin": "are cousins"}
# How two people know each other (#3881), said of the two: "aware" (only heard of each other) never
# claims they met
KNOWS_PAIR_WORDS = {"met": "have met", "aware": "know of each other"}


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


def reads_both_ways(
    predicate: str,
    detail: str = "",
    directional: bool = False,
    both_ways: bool | None = None,
    route_state: str = "",
) -> bool:
    """Does the connection read the same from either name? `both_ways` is the DM's own direction
    for it (#4154; None: its kind's default, `BOTH_WAYS`). Never a family connection whose role
    the DM gave a direction: "niece" is true of one name only (#2457), whatever the mark says."""
    if predicate == KIN and directional and kin.directional(detail):
        return False
    if predicate == "next_to" and next_words.one_way(detail):
        return False  # "on the shore of" reads from the place beside, whatever the mark (#4173 6a)
    if predicate == route_words.ROUTE_TO and route_state:
        return False  # a journey or a plan reads from where the party set out (#4173 6d)
    return predicate in BOTH_WAYS if both_ways is None else both_ways


def pair_words(predicate: str, detail: str = "", knows_how: str | None = None) -> str:
    """How a two-way connection reads with both names first: "are rivals" in "Cerric and Wit are
    rivals" (#4154). The catch-all with the table's own words, and any other kind the DM made
    two-way, says it is a connection and quotes its one-way words: "are connected (“sworn on the
    ashes of”)". Only for a connection `reads_both_ways`; one-way lines keep `link_words`."""
    if predicate == KIN and (role := kin.role_of(detail)) in KIN_PAIR_WORDS:
        return KIN_PAIR_WORDS[role]
    if predicate == "knows" and knows_how:
        return KNOWS_PAIR_WORDS.get(knows_how, PAIR_WORDS["knows"])
    if predicate == "next_to":
        return next_words.pair(detail)
    if predicate == "route_to":
        return route_words.pair(detail)
    if predicate == FALLBACK_PREDICATE and detail:
        return f"{PAIR_WORDS[FALLBACK_PREDICATE]} ({quoted(detail)})"
    if predicate in PAIR_WORDS:
        return PAIR_WORDS[predicate]
    return f"{PAIR_WORDS[FALLBACK_PREDICATE]} ({quoted(connection_words(predicate))})"


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
    # Two places' own words read back as their kind and word (#4173 slice 6d; the website's
    # Next to… and Travel to… send the card's words): "is on the shore of", "is connected by road
    # to", never the catch-all labelled with them
    # (not plain "is on": too short to be sure it means two places)
    for word in (next_words.BORDERS, next_words.SHORE, next_words.COAST, next_words.FOOT):
        if clean.casefold() == next_words.forward(word).casefold():
            return "next_to", word
    for means in route_words.MEANS:
        if clean.casefold() == route_words.forward(means).casefold():
            return route_words.ROUTE_TO, means
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
    route_state: str = "",
) -> str:
    """How one connection reads from its first name to its second. On the catch-all, the words it
    was said in ("sworn on the ashes of") are the connection's own label (#1303); on family, the
    role ("is the niece of"). A place tie reads by its word ("lives in"), and for something that
    moves with no word, "was last seen in", never "is in" (#3363); `subject_kind` is the first
    name's kind ("" when the caller does not know it, which keeps the old "is in").

    `feeling` says the reader is the DM: how a name feels about another is the DM's Reputation, so
    "knows (hostile)" is worded only for a DM-only screen, and everyone else reads plain "knows"
    (#3610). `knows_how` (#3881): the row's word on whether they've met, so a `knows` reads "has
    met", "has heard of" or plain "knows" (the story doesn't say); None where the caller has no row
    (a menu of kinds), which is plain "knows" too."""
    predicate, detail = for_reader(predicate, detail, feeling=feeling)
    knows = (
        KNOWS_HOW_WORDS.get(knows_how, "knows")
        if predicate == "knows" and knows_how is not None
        else "knows"
    )
    if predicate == "located_in":
        return place_words.forward(detail, subject_kind)
    if predicate == "next_to":
        return next_words.forward(detail)
    if predicate == "route_to":  # (a journey or a plan by its state, #4173 6d)
        return route_words.state_forward(detail, route_state)
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
    predicate: str,
    detail: str,
    directional: bool = False,
    subject_kind: str = "",
    *,
    knows_how: str | None = None,
    both_ways: bool | None = None,
    route_state: str = "",
) -> str | None:
    """How a connection reads from its second name, when that differs from `link_words`: "has a
    niece". None for one that reads the same either way, or has no fixed reading back.
    `knows_how` (#3881): two people who met read "has met" from either side. `both_ways` (#4154):
    a two-way kind the DM made one-way reads `ONE_WAY_BACK` ("is seen as a rival by")."""
    if predicate == "route_to":  # (its means both ways, or "is reached by road from": #4173 6d)
        return route_words.state_backward(detail, route_state, one_way=both_ways is False)
    if (
        both_ways is False
        and predicate in ONE_WAY_BACK
        and not (predicate == KIN and directional and kin.directional(detail))
    ):
        return ONE_WAY_BACK[predicate]
    if predicate == "knows" and knows_how:
        return KNOWS_HOW_BACK.get(knows_how) or CONNECTION_BACK.get(predicate)
    if predicate == "located_in":
        return place_words.backward(detail, subject_kind)
    if predicate == KIN and directional and kin.directional(detail):
        return kin.backward(kin.role_of(detail) or "")
    return CONNECTION_BACK.get(predicate) or campaign_back(predicate)
