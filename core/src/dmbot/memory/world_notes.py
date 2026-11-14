"""World notes (#4285, slice 1; Supervisor's ruling on #4285, 2026-11-14): what DMbot guessed about
the world and the DM hasn't checked yet, gathered in one list for the DM. Today the guesses wait on
each place's card ("DMbot read this") and each character's languages; the list is the same rows,
never a copy, so a ✅ here is the card's ✅ too.

- **Places:** a place's land, climate and who rules it, as DMbot read them (their rows still
  `read`, open), through `map_fact_store`, the table's one reader.
- **Languages:** a language DMbot read for a character (`memory_story_languages`, `given_by =
  'read'`), for a character with no sheet (a sheet's languages are the sheet's, #4229). A language
  question waiting in ❓ Questions for you is never shown there once a story row holds it
  (`trait_offers._STILL_OPEN`), so nothing is asked twice.

Each line's id says which table it is (`place:` or `language:`), so one action word takes both.
The DM's own list: never a player's, never on the DM screen (the caller checks who asks). Ids and
words of the list only, never a line of lore or transcript; logs get counts."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory import map_fact_store
from dmbot.memory._changes import ENTITIES, SHEETS, STORY_LANGUAGES, Changes, Scope
from dmbot.memory.models import DM, MemoryRuleError

PLACE, LANGUAGE = "place", "language"
KINDS = (PLACE, LANGUAGE)
GONE = "That note is gone already: it was checked or changed meanwhile."


@dataclass(frozen=True, slots=True)
class Note:
    """One line of the list. `layer`/`value`/`how` for a place (the value is the group's id for
    who rules it); `value` is the language's name for a language. `session_at`: the session it was
    read from (None for an upload or the lore); `made_at`: when DMbot kept it."""

    id: str
    kind: str
    entity_id: str
    name: str
    value: str
    layer: str = ""
    how: str = ""
    session_at: int | None = None
    made_at: int = 0


def note_id(kind: str, row_id: str) -> str:
    return f"{kind}:{row_id}"


def split_id(line_id: str) -> tuple[str, str]:
    """(kind, row id), or MemoryRuleError for an id that is neither (a hand-made request)."""
    kind, _, row_id = line_id.partition(":")
    if kind not in KINDS or not row_id:
        raise MemoryRuleError(GONE)
    return kind, row_id


async def read(scope: Scope) -> list[Note]:
    """Every open note of the campaign, places then languages, each oldest first. Only lines on
    an entry the DM keeps (confirmed or waiting, never one turned down or merged away)."""
    names = {
        str(r["id"]): str(r["name"])
        for r in await scope.select(
            ENTITIES, " AND status IN ('confirmed', 'proposed') AND merged_into IS NULL", []
        )
    }
    out: list[Note] = []
    for fact, session_at, made_at in await map_fact_store.read_notes(scope):
        if fact.place_id in names:
            out.append(
                Note(
                    note_id(PLACE, fact.id),
                    PLACE,
                    fact.place_id,
                    names[fact.place_id],
                    fact.value,
                    fact.layer,
                    fact.how,
                    session_at,
                    made_at,
                )
            )
    sheets = {str(r["entity_id"]) for r in await scope.select(SHEETS, "", [])}
    for r in await scope.select(
        STORY_LANGUAGES, " AND given_by = 'read' ORDER BY created_at, id", []
    ):
        who = str(r["entity_id"])
        if who in names and who not in sheets:
            out.append(
                Note(
                    note_id(LANGUAGE, str(r["id"])),
                    LANGUAGE,
                    who,
                    names[who],
                    str(r["name"]),
                    made_at=int(r["created_at"]),
                )
            )
    return out


async def keep(w: Changes, line_ids: Collection[str]) -> int:
    """The DM's ✅ on these lines, in the caller's one write (one Undo): a place's reading and a
    language each become the DM's own word. A line checked meanwhile is left. How many kept."""
    places, languages = _split_all(line_ids)
    kept = await map_fact_store.keep_rows(w, places)
    if languages:
        for r in await w.select(
            STORY_LANGUAGES, " AND id = ANY(%s) AND given_by = 'read'", [sorted(languages)]
        ):
            await w.update(STORY_LANGUAGES, str(r["id"]), {"given_by": DM})
            kept += 1
    return kept


async def turn_down(w: Changes, line_id: str) -> None:
    """The DM's 🚫 Not right on one line (one Undo). A place's reading is turned down, so it is
    never read in again. A language is taken away, as the character's own ✖ does (the story keeps
    no "not this" for a language; a later lore read may offer it again)."""
    kind, row_id = split_id(line_id)
    if kind == PLACE:
        if not await map_fact_store.reject_row(w, row_id):
            raise MemoryRuleError(GONE)
        return
    rows = await w.select(STORY_LANGUAGES, " AND id = %s AND given_by = 'read'", [row_id])
    if not rows:
        raise MemoryRuleError(GONE)
    await w.delete(STORY_LANGUAGES, row_id)


def of_kind(notes: Sequence[Note], kind: str) -> list[str]:
    """The ids of one group's lines, for ✅ Keep all."""
    if kind not in KINDS:
        raise MemoryRuleError(GONE)
    return [n.id for n in notes if n.kind == kind]


def _split_all(line_ids: Collection[str]) -> tuple[set[str], set[str]]:
    places: set[str] = set()
    languages: set[str] = set()
    for line_id in line_ids:
        kind, row_id = split_id(line_id)
        (places if kind == PLACE else languages).add(row_id)
    return places, languages


__all__ = ["GONE", "KINDS", "LANGUAGE", "PLACE", "Note", "keep", "of_kind", "read", "turn_down"]
