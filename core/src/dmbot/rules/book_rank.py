"""Where a DM's shared book ranks next to the free rules (#1563; docs/PLAN.md, "Rules sources").

A shared rulebook is a sourcebook in a ruleset, not a house rule and not the DM's own homebrew.
The order is: house rules, the campaign's own homebrew (not from a book), followed packs, then
the free rules and the book entries together, each in its edition's place: a 2024 book entry
sits with the target ruleset when that is 2024, a 2014 one with the fallback when that is 2014,
and one for another edition ("other": a supplement, or an entry loaded before the question was
asked) between the two. Pure: no Discord, no database."""

from __future__ import annotations

from dmbot.rules.homebrew import Homebrew
from dmbot.rules.index import Hit

TARGET, BETWEEN, FALLBACK = 0, 1, 2  # nearer the target ruleset ranks first


def book_place(entry: Homebrew, target: str, fallback: str) -> int:
    """Where a book entry sits: with the target ruleset, with the fallback, or between."""
    edition = entry.book_edition or "other"
    if edition == target:
        return TARGET
    if edition == fallback:
        return FALLBACK
    return BETWEEN


def book_wins(entry: Homebrew, hit: Hit | None, target: str, fallback: str) -> bool:
    """Does this book entry answer, rather than the free rules' entry of the same name? Not
    from a book: always (homebrew comes before the free rules). From a book: when it ranks at
    or ahead of the free rules' hit; a tie at the same edition goes to the book, the DM's own
    copy. With no free-rules entry the book answers."""
    if not entry.from_book or hit is None:
        return True
    return book_place(entry, target, fallback) <= (FALLBACK if hit.from_fallback else TARGET)


def book_is_legacy(entry: Homebrew, target: str) -> bool:
    """Tag it `[Legacy 2014]` like a fallback hit: a 2014 book while the target is newer."""
    return entry.from_book and entry.book_edition == "2014" and target != "2014"
