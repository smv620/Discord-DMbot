"""A connection between two names, in plain words (the fixed core of the memory rules). One place
for the Discord name card and the website's relationship web (#2323), so they say the same thing.
Pure: no Discord, no database."""

from __future__ import annotations

from dmbot.memory.ontology import FALLBACK_PREDICATE

CONNECTION_WORDS = {
    "located_in": "is in",
    "member_of": "is a member of",
    "ally_of": "is a friend or ally of",
    "enemy_of": "is an enemy of",
    "kin_of": "is family of",
    "owns": "owns",
    "knows": "knows",
    "serves": "works for",
    "carries": "carries",
}
# The same connection read from the other name's card ("Frostwolf tribe has as members
# Ulfgar"). Connections that read the same both ways use CONNECTION_WORDS.
CONNECTION_BACK = {
    "located_in": "is where you find",
    "member_of": "has as a member",
    "owns": "is owned by",
    "knows": "is known to",
    "serves": "is the boss of",
    "carries": "is carried by",
}
# On a card, where several names may follow ("members include Ulfgar, Kesh").
CARD_BACK = {**CONNECTION_BACK, "member_of": "members include"}
BOTH_WAYS = frozenset({"ally_of", "enemy_of", "kin_of"})


def connection_words(predicate: str) -> str:
    return CONNECTION_WORDS.get(predicate, predicate.replace("_", " "))


def predicate_for(words: str) -> tuple[str, str]:
    """(predicate, detail) for connection words a DM typed or picked on the website: the words of a
    fixed connection ("is a member of") give that connection; anything else is the catch-all with
    the words as its label, exactly as a connection said at the table is kept (#1303)."""
    clean = " ".join(words.split())
    for predicate, known in CONNECTION_WORDS.items():
        if clean.casefold() == known.casefold():
            return predicate, ""
    return FALLBACK_PREDICATE, clean


def link_words(predicate: str, detail: str) -> str:
    """How one connection reads from its first name to its second. On the catch-all, the words it
    was said in ("sworn on the ashes of") are the connection's own label (#1303)."""
    if predicate == FALLBACK_PREDICATE and detail:
        return detail
    return connection_words(predicate)
