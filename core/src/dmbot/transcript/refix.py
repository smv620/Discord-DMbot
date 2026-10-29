"""Re-checking earlier lines after the DM's answer (#127, docs/PLAN.md, "Transcript Cleaner":
"Learning is immediate"). Pure: no Discord, no database, no AI.

When the DM answers "Did they mean…?" with a name, the spelling they chose is a rule from then
on. This finds the *earlier* lines of the same session that say the same words, so they can be
written the same way. It is deliberately narrow, because a wrong fix is worse than a missed one:

- only the DM's own spelling for exactly those heard words (no new guess by sound, whatever the
  scene is now);
- only a line that still says what was heard (a line with any other fix or answer is left);
- only if the name would not be, or stand next to, a secret name (the same check as every fix,
  on the line as written);
- at most `MAX_LINES` lines, the newest first.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import name_key
from dmbot.transcript.cleaner import DM_FIX, clean, safe_answer
from dmbot.transcript.fix_notes import rewrite
from dmbot.transcript.models import Line

MAX_LINES = 100  # earlier lines changed by one answer; the DM can ask again for the rest
MAX_CLEANED = 400  # lines run through the Cleaner for one answer (the slow part), then stop


@dataclass(frozen=True, slots=True)
class Refix:
    """One earlier line to write again: where the heard words are, as heard, and the line's
    new words."""

    speaker: int
    started_ms: int
    heard: str
    edits: tuple[tuple[int, int, str], ...]  # (start, end, written), in order, never overlapping
    text: str


def plan(
    lookup: CampaignLookup,
    lines: Iterable[Line],
    said: str,
    *,
    people: Collection[str] = (),
    limit: int = MAX_LINES,
    max_cleaned: int = MAX_CLEANED,
) -> list[Refix]:
    """The earlier lines (newest first) that say `said` and can be written with the DM's
    spelling for it now. `lookup` must already hold the DM's answer."""
    wanted = name_key(said)
    if not wanted:
        return []
    mine = said.casefold()
    found: list[Refix] = []
    cleaned = 0
    for line in sorted(lines, key=lambda x: x.started_ms, reverse=True):
        if line.sidebar or line.typed or line.text != line.heard:
            continue  # only table speech that is still exactly as heard
        if mine not in line.heard.casefold():
            continue  # cheap check first: cleaning a line is the slow part
        if cleaned == max_cleaned:
            break
        cleaned += 1
        result = clean(lookup, line.heard, people=people)
        picks = [f for f in result.fixes if f.how == DM_FIX and name_key(f.heard) == wanted]
        edits: list[tuple[int, int, str]] = []
        for pick in picks:
            # Checked alone, on the line as written, and next to the picks already taken.
            earlier = tuple(f for f in picks if (f.start, f.end) in {(e[0], e[1]) for e in edits})
            if safe_answer(lookup, line.heard, earlier, pick.start, pick.end, pick.written):
                edits.append((pick.start, pick.end, pick.written))
        if not edits:
            continue
        found.append(
            Refix(
                line.user_id, line.started_ms, line.heard, tuple(edits), rewrite(line.heard, edits)
            )
        )
        if len(found) == limit:
            break
    return found
