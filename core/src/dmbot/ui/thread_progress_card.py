"""🧵 Progress to check (#4522, quests slice 5b part 3; design `docs/design/quests.md` §9.5 and
§10.1): the words of the 📜 card's second stage, what a session's talk changed on a story thread
the campaign already has, one thread at a time, for the DM to keep or turn down (`ThreadQuestions`
draws it once no new thread waits; `memory.thread_progress` holds the lines).

Each line says what would change in the 🧵 Threads card's own words (`ui/thread_parts`): a new step,
something in the way, a reward given, help, a time limit's words, a step done, the thread happening
now, a clue found and by whom (#4624 6c), with the change first ("Done: find Garret"). The DM
unticks what didn't happen; the rest is kept and the unticked ones turned down in one change, and ↩
Undo takes it all back. Private to the DM, as the whole card is; no transcript words are shown: the
session it was heard in, by its date. At most `SHOWN` lines a card (a menu holds 25), so the DM
always sees what a press answers.
Pure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory import thread_progress as prog
from dmbot.ui import logic
from dmbot.ui.names import _md

HEADING = "🧵 **Progress to check**"
# (the ux-critic: short enough for a phone, and 🚫 as on "🚫 Not a story thread")
KEEP, NONE, SKIP = "✅ Keep these", "🚫 None happened", "Skip ›"
HOLDER = "What happened? (untick the rest)"
HELP = (
    "Untick what didn't happen, then press ✅ Keep these. DMbot won't offer unticked ones again; "
    "↩ Undo takes it all back."
)
LINE_MAX = 90
SHOWN = 25  # lines on one card: a menu holds 25, and a press answers only what the DM sees
# (room under Discord's 2,000 for what the card adds in front: "✅ Kept … on **name**", as the
# first stage's `CARD_MAX`; past it the last lines fold into "…and N more", the help kept)
CARD_MAX = MESSAGE_MAX - 300


def line_text(change: prog.Change, part_words: Mapping[str, str], kind: str = "") -> str:
    """One line, the change first so a phone's menu still shows it (the ux-critic), in the 🧵
    card's own words: "New · Step: find the map", "Done: find Garret", "This thread: happening
    now" (a promise in a promise's words, "This promise: kept"). `part_words`: the thread's parts
    by id, for a part moving on; `kind`: the thread's kind."""
    from dmbot.memory.threads import PROMISE, words_for
    from dmbot.ui.thread_parts import KIND_WORDS, STATE_SAID

    words = logic.shorten(change.words, LINE_MAX)
    if change.kind == prog.PART:
        text = f"New · {KIND_WORDS.get(change.part_kind, change.part_kind)}: {words}"
        if change.state == "lent":
            text += " (lent)"
        if change.due:
            text += f" (by: {logic.shorten(change.due, LINE_MAX)})"
        return text
    if change.kind == prog.PART_STATE:
        part = logic.shorten(part_words.get(change.part_id or "", "a step"), LINE_MAX)
        return f"{STATE_SAID.get(change.state, change.state).capitalize()}: {part}"
    if change.kind == prog.CLUE:
        return clue_text(change, part_words)
    which = "promise" if kind == PROMISE else "thread"
    return f"This {which}: {words_for(kind).get(change.state, change.state).lower()}"


def _and(names: Sequence[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def clue_text(change: prog.Change, names: Mapping[str, str]) -> str:
    """A session's "clue found" (#4624 6c): "Clue found: the torn letter (by Okthandom)", or, for
    a clue the campaign has, "Clue found: the torn letter (now known by Cerric)". `names`: the
    clue's and the finders' names by id (a gone one is left out)."""
    clue = names.get(change.clue_id or "") if change.clue_id else change.words
    who = [n for f in change.finder_ids if (n := names.get(f))]
    name = logic.shorten(clue or "a clue", LINE_MAX)
    text = f"Clue found: {name}"
    if not who:  # (every finder gone: a clue the campaign has must not read as a new one)
        return f"Clue found again: {name}" if change.clue_id else text
    said = _and(who)
    if change.clue_id:  # (the campaign's clue: who knows it now, never a claim about before)
        return f"{text} (now known by {said})"
    return f"{text} (by {said})"


def card_text(
    thread_name: str,
    lines: Sequence[prog.Line],
    part_words: Mapping[str, str],
    on: frozenset[str],
    *,
    at: int,
    of: int,
    heard: str,
    kind: str = "",
    sessions: int = 1,
) -> str:
    """The card for one thread: a question (the DM decides), where it was heard, each line
    (struck through when unticked), and what the buttons do. Never posted in a shared channel."""
    count = f" (thread {at} of {of})" if of > 1 else ""
    if sessions > 1:
        where = f"{sessions} sessions"
    else:
        where = f"**{_md(heard)}**" if heard else "a session"
    head = [
        f"{HEADING}{count}",
        f"**{_md(logic.shorten(thread_name, LINE_MAX))}**: did these happen? DMbot heard them in "
        f"{where}.",
    ]
    rows = []
    for line in lines:
        said = _md(line_text(line.change, part_words, kind))
        rows.append(f"• {said}" if line.id in on else f"• ~~{said}~~ (left out)")
    shown = list(rows)
    text = "\n".join([*head, *shown, HELP])
    while len(text) > CARD_MAX and shown:  # (fold the last lines; the menu still has them)
        shown = shown[:-1]
        more = f"• …and {len(rows) - len(shown)} more: see the menu below."
        text = "\n".join([*head, *shown, more, HELP])
    return fit_message(text)


NONE_FIT = "Those no longer fit the thread (it changed since), so nothing was kept."


def kept_text(name: str, n: int) -> str:
    many = "change" if n == 1 else "changes"
    return f"✅ Kept {n} {many} on **{_md(name)}**. See them on its 🧵 card."


def none_text(name: str) -> str:
    return f"🚫 Left as it was: **{_md(name)}**. DMbot won't offer those again."


def undone_text(name: str) -> str:
    return f"↩ Undone: the changes on **{_md(name)}** are waiting again."


__all__ = [
    "HEADING",
    "HOLDER",
    "KEEP",
    "NONE",
    "SHOWN",
    "SKIP",
    "card_text",
    "kept_text",
    "line_text",
    "none_text",
    "undone_text",
]
