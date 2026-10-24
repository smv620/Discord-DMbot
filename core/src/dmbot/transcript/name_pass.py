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
from collections.abc import Iterable, Sequence
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
# Two offered names this close in likeness (or sharing a sound) are too alike to choose between
# in code, whatever PlotBot says: the word goes to the DM instead (#2628 review).
AMBIGUOUS_MARGIN = 0.08

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


@dataclass(frozen=True, slots=True)
class Survey:
    items: list[Candidate]  # the words to ask PlotBot about, most heard first
    guarded: int  # words left as heard because they are close to a secret (or DM-only) name
    new: int  # words close to no list name: new names, for the after-session scan and the DM


def survey(lines: Sequence[str], lookup: CampaignLookup, people: Iterable[str] = ()) -> Survey:
    """The words in `lines` (cleaned text) worth asking PlotBot about, most heard first, and how
    many were left alone. `people`: the names of the people at the table, each whole name and each
    word of it."""
    skip = {entry.key for entry in lookup.names} | set(lookup.keep_keys)
    skip |= {key for key in lookup.people_by_key}
    skip |= {p.casefold() for p in people}
    found = group_alike(find_new_names(lines, skip, min_times=1, unlimited=True))
    out: list[Candidate] = []
    guarded = new = 0
    for suggestion in found:
        spellings = (suggestion.name, *suggestion.also)
        if sum(c.isalpha() for c in suggestion.name) < MIN_LETTERS:
            continue
        if lookup.keep_as_heard(suggestion.name):
            continue
        if text_names_a_secret(lookup, suggestion.name):
            guarded += 1
            continue
        near: dict[str, Near] = {}
        secret = False
        for spelling in spellings:
            ranked, close_to_secret = _near(lookup, spelling)
            secret = secret or close_to_secret
            for n in ranked:
                if n.entity_id not in near or n.likeness > near[n.entity_id].likeness:
                    near[n.entity_id] = n
        if secret:
            guarded += 1  # near a secret name: never asked about, never written
            continue
        if not near:
            new += 1  # near nothing: a new name, for the scan and the DM
            continue
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
    return Survey(out[:MAX_CANDIDATES], guarded, new)


def candidates(
    lines: Sequence[str], lookup: CampaignLookup, people: Iterable[str] = ()
) -> list[Candidate]:
    return survey(lines, lookup, people).items


def question(cands: Sequence[Candidate]) -> str:
    """The user message: each candidate with the list names it is close to and where it was said."""
    parts = ["Words to decide, each with the listed names it is close to:"]
    for i, c in enumerate(cands, 1):
        listed = "; ".join(f"{n.name} ({n.kind})" for n in c.near)
        shown = " | ".join(f'"{line}"' for line in c.lines)
        parts.append(f'{i}. Heard "{c.heard}" {c.times} times. Listed: {listed}. Said: {shown}')
    return "\n".join(parts)


@dataclass(frozen=True, slots=True)
class Verdict:
    sure: list[Decision]  # written into the cleaned text
    unsure: list[Candidate]  # asked of the DM (a name suggestion with the near names)
    new: list[Candidate]  # PlotBot says a new name: asked of the DM as any new name


def too_alike_to_choose(c: Candidate) -> bool:
    """Whether two or more of the names offered for this word are about as close as each other, or
    sound the same: a sure answer for it is not believed, in code (#2628 review)."""
    if len(c.near) < 2:
        return False
    if c.near[0].likeness - c.near[1].likeness <= AMBIGUOUS_MARGIN:
        return True
    codes = [set(sound_keys(n.name)) for n in c.near]
    return any(a & b for i, a in enumerate(codes) for b in codes[i + 1 :])


def decide(raw: str, cands: Sequence[Candidate]) -> Verdict:
    """PlotBot's reply, checked: a sure answer must name one of those offered for that candidate,
    spelled as listed. Everything else is not written: "a new name" is a new name, and anything
    else (not sure, a name that was not offered, a number that does not exist, a word with no
    answer, a reply that cannot be read) is unsure."""
    answers: dict[int, tuple[str, str]] = {}
    try:
        data = json.loads(raw)
        items = data["answers"]
    except (ValueError, KeyError, TypeError):
        items = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            n, answer, name = item.get("n"), item.get("answer"), item.get("name")
            if (
                type(n) is int
                and 1 <= n <= len(cands)
                and n not in answers
                and isinstance(answer, str)
                and isinstance(name, str)
            ):
                answers[n] = (answer, name)
    sure: list[Decision] = []
    unsure: list[Candidate] = []
    new: list[Candidate] = []
    for n, c in enumerate(cands, 1):
        answer, name = answers.get(n, (UNSURE, ""))
        match = next((x for x in c.near if x.name.casefold() == name.strip().casefold()), None)
        if answer == SURE and match is not None and not too_alike_to_choose(c):
            sure.append(Decision(c.heard, c.also, match.entity_id, match.name, c.times))
        elif answer == NEW:
            new.append(c)
        else:
            unsure.append(c)
    return Verdict(sure, unsure, new)


BATCH_BASE = 1_000_000_000  # (a name-pass batch never equals a memory change's small number)


def batch_of(session_id: str, entity_id: str = "") -> int:
    """The Undo number of one name's fixes in a session: apart from the memory log's own batch
    numbers, and the same every time for the same session and name."""
    digest = hashlib.sha256(f"{session_id}:{entity_id}".encode()).digest()
    return BATCH_BASE + int.from_bytes(digest[:6], "big") % BATCH_BASE


@dataclass(frozen=True, slots=True)
class Fixed:
    name: str  # the list name written
    forms: tuple[tuple[str, int], ...]  # the spellings it replaced, with how many times each
    batch: int  # for its Undo

    @property
    def times(self) -> int:
        return sum(n for _, n in self.forms)


@dataclass(frozen=True, slots=True)
class Report:
    """What the pass did, for the DM and the dev tool: counts and the spellings as heard (the
    forms are words from a transcript: only the campaign's DM and dev1's terminal see them)."""

    fixed: tuple[Fixed, ...] = ()
    unsure: tuple[Candidate, ...] = ()
    new: tuple[Candidate, ...] = ()  # PlotBot says a new name: offered to the DM as any new name
    guarded: int = 0
    cut: bool = (
        False  # the model's answer stopped at its limit: the words it did not reach are unsure
    )

    @property
    def quiet(self) -> bool:
        return not (self.fixed or self.unsure or self.new or self.guarded or self.cut)


# PlotBot's answer stopped at its limit (#2649): every word it did not reach is "not sure", which
# is safe, and the DM is told why so many are.
CUT_LINE = "PlotBot's answer stopped early, so more words than usual are marked not sure."
MAX_FORMS_SHOWN = 6  # spellings listed under one name
MAX_NAMES_SHOWN = 12


def times(n: int) -> str:
    return f"{n} time" if n == 1 else f"{n} times"


def report_text(report: Report) -> str:
    """The report as the DM reads it (and dev1 on the terminal)."""
    lines = ["**Names fixed this session**"]
    for fixed in report.fixed[:MAX_NAMES_SHOWN]:
        lines.append(f"{fixed.name}: corrected {times(fixed.times)}")
        for form, n in fixed.forms[:MAX_FORMS_SHOWN]:
            lines.append(f"  ({n}) {form}")
        if len(fixed.forms) > MAX_FORMS_SHOWN:
            lines.append(f"  …and {len(fixed.forms) - MAX_FORMS_SHOWN} more spellings")
    if len(report.fixed) > MAX_NAMES_SHOWN:
        lines.append(f"…and {len(report.fixed) - MAX_NAMES_SHOWN} more names")
    lines.append(f"Not sure (asked the DM): {len(report.unsure)}")
    for c in report.unsure[:MAX_NAMES_SHOWN]:
        options = " or ".join(n.name for n in c.near)
        lines.append(f"  ({c.times}) {c.heard} → {options}?")
    lines.append(f"New names (asked the DM): {len(report.new)}")
    lines.append(f"Left as heard (secret-name guard): {report.guarded}")
    if report.cut:
        lines.append(CUT_LINE)
    return "\n".join(lines)


def import_text(reports: Sequence[Report]) -> str:
    """One report for a whole import (#2649), not one for each session: the names fixed across
    its sessions (each name once, with its spellings summed), then the other counts added up. Only
    the sessions that had something are in `reports`."""
    by_name: dict[str, list[Fixed]] = {}
    for report in reports:
        for fixed in report.fixed:
            by_name.setdefault(fixed.name, []).append(fixed)
    lines = [f"**Names fixed in your imported sessions** ({len(reports)} had something)"]
    ordered = sorted(
        by_name.items(), key=lambda kv: (-sum(f.times for f in kv[1]), kv[0].casefold())
    )
    for name, fixes in ordered[:MAX_NAMES_SHOWN]:
        total = sum(f.times for f in fixes)
        where = f" in {len(fixes)} sessions" if len(fixes) > 1 else ""
        lines.append(f"{name}: corrected {times(total)}{where}")
        forms = group_forms(pair for f in fixes for pair in f.forms)
        for form, n in forms[:MAX_FORMS_SHOWN]:
            lines.append(f"  ({n}) {form}")
        if len(forms) > MAX_FORMS_SHOWN:
            lines.append(f"  …and {len(forms) - MAX_FORMS_SHOWN} more spellings")
    if len(ordered) > MAX_NAMES_SHOWN:
        lines.append(f"…and {len(ordered) - MAX_NAMES_SHOWN} more names")
    lines.append(f"Not sure (asked the DM): {sum(len(r.unsure) for r in reports)}")
    lines.append(f"New names (asked the DM): {sum(len(r.new) for r in reports)}")
    lines.append(f"Left as heard (secret-name guard): {sum(r.guarded for r in reports)}")
    if any(r.cut for r in reports):
        lines.append(CUT_LINE)
    return "\n".join(lines)


def counts_line(report: Report) -> str:
    """The numbers only, safe for logs and the testing logs: no spelling in it."""
    return (
        f"{len(report.fixed)} names, {sum(f.times for f in report.fixed)} fixes, "
        f"{len(report.unsure)} unsure, {len(report.new)} new, {report.guarded} guarded"
        + (", answer cut off" if report.cut else "")
    )


def group_forms(pairs: Iterable[tuple[str, int]]) -> tuple[tuple[str, int], ...]:
    """Spellings counted without regard to case ("keirandal" and "Keirandal" are one), shown as
    written most often, most heard first."""
    shown: dict[str, dict[str, int]] = {}
    for form, n in pairs:
        shown.setdefault(form.casefold(), {}).setdefault(form, 0)
        shown[form.casefold()][form] += n
    grouped = [
        (max(forms, key=lambda f: (forms[f], f)), sum(forms.values())) for forms in shown.values()
    ]
    grouped.sort(key=lambda t: (-t[1], t[0].casefold()))
    return tuple(grouped)
