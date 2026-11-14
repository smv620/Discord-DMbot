"""World notes (#4285, slice 1; Supervisor's ruling on #4285, 2026-11-14): what DMbot guessed about
the world and the DM hasn't checked yet, gathered in one list for the DM. Today the guesses wait on
each place's card ("DMbot read this") and each character's languages; the list is the same rows,
never a copy, so a ✅ here is the card's ✅ too.

- **Places:** a place's land, climate and who rules it, as DMbot read them (their rows still
  `read`, open), through `map_fact_store`, the table's one reader.
- **Languages:** a language DMbot read for a character (`memory_story_languages`, `given_by =
  'read'`), for a character with no sheet (a sheet's languages are the sheet's, #4229). A language
  that is a question in ❓ Questions for you (waiting, or answered there) stays there and is never a
  note (Supervisor's ruling on #4285), and DMbot's own read never hides the question
  (`trait_offers._STILL_OPEN`), so nothing is asked twice.

Each line's id says which table it is (`place:` or `language:`), so one action word takes both.
The DM's own list: never a player's, never on the DM screen (the caller checks who asks). Ids and
words of the list only, never a line of lore or transcript; logs get counts."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory import gate, map_fact_store
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import ENTITIES, STORY_LANGUAGES, Changes, Scope
from dmbot.memory.models import DM, MemoryRuleError

PLACE, LANGUAGE = "place", "language"
KINDS = (PLACE, LANGUAGE)
GONE = "That note is gone already: it was checked or changed meanwhile."


@dataclass(frozen=True, slots=True)
class Note:
    """One line of the list. `layer`/`value`/`how` for a place (the value is the group's id for
    who rules it); `value` is the language's name for a language. `session_at`: the session it was
    read from (None for an upload or the lore); `made_at`: when DMbot read it."""

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


# A character's languages from the story that DMbot read and that are no question already: none for
# a character with a sheet (its languages are the sheet's, #4229), none that ❓ Questions for you
# asks or the DM answered there (any offer row, waiting or never: Supervisor's ruling on #4285,
# "language questions stay in Questions for you", so nothing is asked twice).
_LANGUAGE_NOTES = (
    " AND given_by = 'read'"
    " AND NOT EXISTS (SELECT 1 FROM character_sheets h WHERE h.guild_id = memory_story_languages"
    ".guild_id AND h.campaign_id = memory_story_languages.campaign_id"
    " AND h.entity_id = memory_story_languages.entity_id)"
    " AND NOT EXISTS (SELECT 1 FROM memory_trait_offers o WHERE o.guild_id ="
    " memory_story_languages.guild_id AND o.campaign_id = memory_story_languages.campaign_id"
    " AND o.entity_id = memory_story_languages.entity_id AND o.kind = 'language'"
    " AND o.name_key = memory_story_languages.name_key)"
    " ORDER BY created_at, id"
)


async def read(scope: Scope) -> list[Note]:
    """Every open note of the campaign, places then languages, each oldest first. Only lines on
    an entry the DM keeps (confirmed or waiting, never one turned down or merged away), and a
    ruler only when the group is kept too (so ✅ never makes a group the DM turned down rule)."""
    names = {
        str(r["id"]): str(r["name"])
        for r in await scope.select(
            ENTITIES, " AND status IN ('confirmed', 'proposed') AND merged_into IS NULL", []
        )
    }
    out: list[Note] = []
    for fact, session_at, made_at in await map_fact_store.dm_read_notes(
        scope, gate.Viewer.the_dm()
    ):
        if fact.place_id in names and (fact.layer != mf.CONTROL or fact.value in names):
            out.append(
                Note(
                    note_id(PLACE, fact.id), PLACE, fact.place_id, names[fact.place_id],
                    fact.value, fact.layer, fact.how, session_at, made_at,
                )
            )  # fmt: skip
    for r in await scope.select(STORY_LANGUAGES, _LANGUAGE_NOTES, []):
        who = str(r["entity_id"])
        if who in names:
            out.append(
                Note(
                    note_id(LANGUAGE, str(r["id"])), LANGUAGE, who, names[who], str(r["name"]),
                    made_at=int(r["created_at"]),
                )
            )  # fmt: skip
    return out


async def _still_open(w: Changes, line_ids: Collection[str]) -> tuple[set[str], set[str]]:
    """The lines of these that are open notes now, by the list's own rule (an entry turned down
    since, a hand-made id, a language that is a question meanwhile: none of them), split by table.
    MemoryRuleError(GONE) for an id that is neither kind."""
    _split_all(line_ids)
    now = {n.id for n in await read(w)}
    return _split_all([i for i in line_ids if i in now])


async def keep(w: Changes, line_ids: Collection[str]) -> int:
    """The DM's ✅ on these lines, in the caller's one write (one Undo): a place's reading and a
    language each become the DM's own word. A line checked or gone meanwhile is left; when none of
    them is open any more, MemoryRuleError(GONE), as 🚫 says. How many kept."""
    places, languages = await _still_open(w, line_ids)
    if line_ids and not (places or languages):
        raise MemoryRuleError(GONE)
    kept = await map_fact_store.keep_rows(w, places)
    for row_id in sorted(languages):
        await w.update(STORY_LANGUAGES, row_id, {"given_by": DM})
        kept += 1
    return kept


async def turn_down(w: Changes, line_id: str) -> None:
    """The DM's 🚫 Not right on one line (one Undo). A place's reading is turned down, so it is
    never read in again. A language is taken away, as the character's own ✖ does: the story keeps
    no "not this" for a language, so a later lore read may give it again (#4362)."""
    places, languages = await _still_open(w, [line_id])
    if places:
        if not await map_fact_store.reject_row(w, next(iter(places))):
            raise MemoryRuleError(GONE)
        return
    if not languages:
        raise MemoryRuleError(GONE)
    await w.delete(STORY_LANGUAGES, next(iter(languages)))


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


__all__ = [
    "GONE", "KINDS", "LANGUAGE", "PLACE", "Note", "keep", "note_id", "of_kind", "read",
    "split_id", "turn_down",
]  # fmt: skip
