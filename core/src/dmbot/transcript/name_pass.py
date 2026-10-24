"""PlotBot's name pass on a session's cleaned transcript (#2628; docs/PLAN.md, "Transcript
Cleaner"). Pure: no Discord, no database, no AI call; the bot makes the call and writes the fixes.

The owner's rule (2026-10-24): a lore character's name is never spelled wrong. Speech-to-text
writes the names it does not know the way they sound ("Keirandal" for the list's "Kethrandil"),
and the Transcript Cleaner fixes a name silently only when it is nearly certain, which is
deliberate: a wrong fix in a transcript the whole server reads is worse than a missed one. So once
a session is complete, before its recap starts, one pass looks again, with context:

- **Code finds the candidates** (`candidates`): the capitalised words and runs in the cleaned lines
  that the campaign's names list does not have, that are not ordinary words, game terms, people at
  the table or "keep as heard" words, and that are close to a list name by sound or by letters.
  A candidate that is close to a secret name is dropped whole: it is never asked about or written.
  A word close to no list name is a new name, left to the after-session scan and the DM.
- **PlotBot decides** which list name each candidate means, from the list names offered for that
  candidate (never the whole list) and a short line where it was said (`question`, `decide`). The
  answer is "this is <one of the offered names>", "a new name" or "not sure".
- **Only a sure answer is written**, and only into the cleaned text, never the raw one (#933), by
  the same narrow door as a DM's fixed spelling (`transcript.respell`). Anything else changes
  nothing.

The prompt carries a few words of each line around the candidate and nothing else of the
transcript.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.scan import alike, find_new_names, group_alike, sound_keys
from dmbot.transcript.cleaner import text_names_a_secret

MIN_LETTERS = 4  # shorter words sound like too many names
NEAR_BY_SOUND = 0.55  # spelled this alike, and sounding alike
NEAR_BY_LETTERS = 0.7  # or spelled this alike (PlotBot decides, so this may be generous)
MAX_NEAR = 3  # list names offered for one candidate
MAX_LINES = 3  # short lines shown for one candidate
SNIPPET = 70  # characters either side of the candidate
MAX_CANDIDATES = 30  # asked in one call; the most heard first

SURE, NEW, UNSURE = "is", "new", "unsure"

SYSTEM = (
    "You help fix how a name is spelled in a tabletop role-playing game's transcript. "
    "Speech-to-text wrote some names the way they sound. For each numbered word, decide whether "
    "it is one of the campaign's listed names, wrongly spelled.\n"
    "Rules:\n"
    '- Answer "is" only when the listed name is plainly the one meant. Give that name exactly as '
    "it is listed.\n"
    '- Answer "new" when it is a different name that is not on the list (a new person or place).\n'
    '- Answer "unsure" whenever you cannot tell. A wrong fix is worse than a missed one.\n'
    "- Use only the lines shown and the listed names. Never invent a name.\n"
    "Answer with JSON only."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "answer": {"type": "string", "enum": [SURE, NEW, UNSURE]},
                    "name": {"type": "string"},
                },
                "required": ["n", "answer", "name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Near:
    entity_id: str
    name: str  # the entry's own name as written on the list
    kind: str  # what the list says it is: "player character", "place", ...
    likeness: float


@dataclass(frozen=True, slots=True)
class Candidate:
    heard: str  # as heard most often
    also: tuple[str, ...]  # other spellings of it folded in ("Vane" with "Oskar Vane")
    times: int
    near: tuple[Near, ...]  # most alike first
    lines: tuple[str, ...]  # short bits of the lines it was said in


@dataclass(frozen=True, slots=True)
class Decision:
    heard: str
    also: tuple[str, ...]
    entity_id: str
    name: str  # the list name to write
    times: int


def _kind_of(lookup: CampaignLookup, entity_id: str) -> str:
    entity = lookup.entities.get(entity_id)
    if entity is None:
        return "name"
    return "player character" if entity.is_player_character else entity.type.replace("_", " ")


def _near(lookup: CampaignLookup, spelling: str) -> tuple[list[Near], bool]:
    """The list names `spelling` is close to, most alike first (at most `MAX_NEAR`), and whether it
    is also close to a secret name (then it is never asked about)."""
    mine = set(sound_keys(spelling))
    hidden = lookup.hidden
    best: dict[str, Near] = {}
    secret_close = False
    for entry in lookup.names:
        if entry.person:
            continue
        likeness = alike(spelling, entry.text, NEAR_BY_SOUND)
        sounds = bool(mine & set(entry.codes))
        close = (sounds and likeness >= NEAR_BY_SOUND) or likeness >= NEAR_BY_LETTERS
        if not close:
            continue
        if entry.secret or entry.entity_id in hidden:
            secret_close = True  # (an entry only the DM sees is never written into the transcript)
            continue
        if not entry.confirmed:
            continue
        old = best.get(entry.entity_id)
        if old is None or likeness > old.likeness:
            own = (
                lookup.entities[entry.entity_id].name if entry.entity_id in lookup.entities else ""
            )
            best[entry.entity_id] = Near(
                entry.entity_id, own or entry.text, _kind_of(lookup, entry.entity_id), likeness
            )
    ranked = sorted(best.values(), key=lambda n: -n.likeness)[:MAX_NEAR]
    return ranked, secret_close


def _bit(line: str, spelling: str) -> str:
    """A few words either side of the spelling."""
    at = line.casefold().find(spelling.casefold())
    if at < 0:
        return ""
    start, end = max(0, at - SNIPPET), min(len(line), at + len(spelling) + SNIPPET)
    return ("…" if start else "") + line[start:end].strip() + ("…" if end < len(line) else "")


def candidates(
    lines: Sequence[str], lookup: CampaignLookup, people: Iterable[str] = ()
) -> list[Candidate]:
    """The words in `lines` (cleaned text) worth asking PlotBot about, most heard first. `people`:
    the names of the people at the table, each whole name and each word of it."""
    skip = {entry.key for entry in lookup.names} | set(lookup.keep_keys)
    skip |= {key for key in lookup.people_by_key}
    skip |= {p.casefold() for p in people}
    found = group_alike(find_new_names(lines, skip, min_times=1, unlimited=True))
    out: list[Candidate] = []
    for suggestion in found:
        spellings = (suggestion.name, *suggestion.also)
        if sum(c.isalpha() for c in suggestion.name) < MIN_LETTERS:
            continue
        if lookup.keep_as_heard(suggestion.name) or text_names_a_secret(lookup, suggestion.name):
            continue
        near: dict[str, Near] = {}
        secret = False
        for spelling in spellings:
            ranked, close_to_secret = _near(lookup, spelling)
            secret = secret or close_to_secret
            for n in ranked:
                if n.entity_id not in near or n.likeness > near[n.entity_id].likeness:
                    near[n.entity_id] = n
        if secret or not near:
            continue  # near a secret name: never; near nothing: a new name, not for this pass
        shown: list[str] = []
        for line in lines:
            for spelling in spellings:
                bit = _bit(line, spelling)
                if bit:
                    shown.append(bit)
                    break
            if len(shown) >= MAX_LINES:
                break
        out.append(
            Candidate(
                suggestion.name,
                suggestion.also,
                suggestion.times,
                tuple(sorted(near.values(), key=lambda n: -n.likeness)[:MAX_NEAR]),
                tuple(shown),
            )
        )
    out.sort(key=lambda c: (-c.times, c.heard))
    return out[:MAX_CANDIDATES]


def question(cands: Sequence[Candidate]) -> str:
    """The user message: each candidate with the list names it is close to and where it was said."""
    parts = ["Words to decide, each with the listed names it is close to:"]
    for i, c in enumerate(cands, 1):
        listed = "; ".join(f"{n.name} ({n.kind})" for n in c.near)
        shown = " | ".join(f'"{line}"' for line in c.lines)
        parts.append(f'{i}. Heard "{c.heard}" {c.times} times. Listed: {listed}. Said: {shown}')
    return "\n".join(parts)


def decide(raw: str, cands: Sequence[Candidate]) -> list[Decision]:
    """The sure answers in PlotBot's reply, checked: the name must be one of those offered for that
    candidate, spelled as listed. Everything else (a new name, not sure, a name that was not
    offered, a number that does not exist, an answer that cannot be read) changes nothing."""
    try:
        data = json.loads(raw)
        answers = data["answers"]
    except (ValueError, KeyError, TypeError):
        return []
    out: list[Decision] = []
    done: set[int] = set()
    if not isinstance(answers, list):
        return []
    for item in answers:
        if not isinstance(item, dict):
            continue
        n, answer, name = item.get("n"), item.get("answer"), item.get("name")
        if answer != SURE or type(n) is not int or not 1 <= n <= len(cands) or n in done:
            continue
        done.add(n)
        c = cands[n - 1]
        match = next(
            (
                x
                for x in c.near
                if isinstance(name, str) and x.name.casefold() == name.strip().casefold()
            ),
            None,
        )
        if match is None:
            continue
        out.append(Decision(c.heard, c.also, match.entity_id, match.name, c.times))
    return out


BATCH_BASE = 1_000_000_000  # (a name-pass batch never equals a memory change's small number)


def batch_of(session_id: str) -> int:
    """The Undo number of a session's name pass: one for the session, apart from the memory log's
    own batch numbers, and the same every time for the same session."""
    digest = hashlib.sha256(session_id.encode()).digest()
    return BATCH_BASE + int.from_bytes(digest[:6], "big") % BATCH_BASE


def note(done: Collection[tuple[str, str, int]]) -> str:
    """The DM-screen line: names and counts only. `done`: (the list name, the spellings as heard
    joined with " / ", how many lines)."""
    parts = [
        f"{lines} {'spelling' if lines == 1 else 'spellings'} of {name} (heard as {heard})"
        for name, heard, lines in done
    ]
    return "Fixed " + ", ".join(parts) + "."
