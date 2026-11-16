"""🧵 Progress to check (#4522, quests slice 5b part 3; design `docs/design/quests.md` §9.5 and
§10.1): the words of the 📜 card's second stage, what a session's talk changed on a story thread
the campaign already has, one thread at a time, for the DM to keep or turn down (`ThreadQuestions`
draws it once no new thread waits; `memory.thread_progress` holds the lines).

Each line says what would change in the 🧵 Threads card's own words (`ui/thread_parts`): a new
step, something in the way, a reward given, help, a time limit's words, a step done, the thread
happening now. The DM unticks what didn't happen; the rest is kept in one change, and ↩ Undo takes
it all back. Private to the DM, as the whole card is; no transcript words are shown: the session it
was heard in, by its date. Pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dmbot.discord_limits import fit_message
from dmbot.memory import thread_progress as prog
from dmbot.ui import logic
from dmbot.ui.names import _md

HEADING = "🧵 **Progress to check**"
KEEP, NONE, SKIP = "✅ Keep these", "🚫 None of these happened", "Skip ›"
HOLDER = "What happened"
UNTICK = "Untick anything that didn't happen. The rest is kept, in one change."
AFTER = "Kept lines show on the thread's 🧵 card. ↩ Undo takes them all back."
LINE_MAX = 90


def line_text(change: prog.Change, part_words: Mapping[str, str]) -> str:
    """One line in the 🧵 card's own words: "New step: find the map", "Step "find Garret": done",
    "The thread: happening now". `part_words`: the thread's parts by id, for a part moving on."""
    from dmbot.memory.threads import STATE_WORDS
    from dmbot.ui.thread_parts import KIND_WORDS, STATE_SAID

    words = logic.shorten(change.words, LINE_MAX)
    if change.kind == prog.PART:
        kind = KIND_WORDS.get(change.part_kind, change.part_kind)
        text = f"New: {kind.lower()}, {words}"
        if change.state == "lent":
            text += " (lent: to give back)"
        if change.due:
            text += f" (when: {logic.shorten(change.due, LINE_MAX)})"
        return text
    if change.kind == prog.PART_STATE:
        part = logic.shorten(part_words.get(change.part_id or "", "a step"), LINE_MAX)
        return f"“{part}”: {STATE_SAID.get(change.state, change.state)}"
    return f"The thread: {STATE_WORDS.get(change.state, change.state).lower()}"


def card_text(
    thread_name: str,
    lines: Sequence[prog.Line],
    part_words: Mapping[str, str],
    on: frozenset[str],
    *,
    at: int,
    of: int,
    heard: str,
) -> str:
    """The card for one thread: its name, where it was heard, each line (struck through when
    unticked), what Keep does. Never posted in a shared channel."""
    count = f" ({at} of {of} threads)" if of > 1 else ""
    where = f"heard in **{_md(heard)}**" if heard else "heard in a session"
    out = [f"{HEADING}{count}", f"On **{_md(thread_name)}**, DMbot {where}:"]
    for line in lines:
        said = _md(line_text(line.change, part_words))
        out.append(f"• {said}" if line.id in on else f"• ~~{said}~~ (left out)")
    out += [UNTICK, AFTER]
    return fit_message("\n".join(out))


def kept_text(name: str, n: int) -> str:
    return f"✅ Kept {n} {'change' if n == 1 else 'changes'} on **{_md(name)}**."


def none_text(name: str) -> str:
    return f"🚫 Left as it was: **{_md(name)}**. DMbot won't offer those again."


def undone_text(name: str) -> str:
    return f"↩ Undone: the changes on **{_md(name)}** are waiting again."


__all__ = [
    "HEADING",
    "HOLDER",
    "KEEP",
    "NONE",
    "SKIP",
    "card_text",
    "kept_text",
    "line_text",
    "none_text",
    "undone_text",
]
