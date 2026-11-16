"""The words of one line in a story-thread suggestion (#4501): "100 gold" / "Reward from Hlin
Trollbane, when the job's done". Discord's 📜 card words them in `ui/thread_questions.line_for`;
the website's API may not import the Discord screens (an import contract), so the same words are
here, and a test holds the two equal for every kind and state, so they cannot drift."""

from __future__ import annotations

from collections.abc import Mapping

from dmbot.discord_limits import shorten
from dmbot.memory import thread_parts as tp
from dmbot.memory.models import HINTED
from dmbot.memory.thread_read import ReadBranch, ReadPart

KIND_WORDS = {
    tp.OBJECTIVE: "Step", tp.COMPLICATION: "In the way", tp.REWARD: "Reward",
    tp.REWARD_GIVEN: "Reward given", tp.HELP: "Help", tp.TIME_LIMIT: "Time limit",
}  # fmt: skip
STATE_SAID = {
    "to_keep": "theirs to keep",
    "lent": "lent",
    "returned": "returned",
    "not_returned": "not returned",
}  # fmt: skip  (a help's state: the only one a line says)
DUE_SAID = {tp.UP_FRONT: "up front", tp.AFTERWARDS: "when the job's done"}
LIMIT_SAID = {
    tp.HARD: "hard: miss it and something's lost",
    tp.SOFT: "soft: miss it and things get worse",
}
BRANCH_WORD = "🌿 Branch"
HINT_SAID = "just a hint: your lore leaves it open"
NOT_KEPT_SHORT = "name not kept yet, left out"
LINE_WORDS_MAX, TERMS_SHOWN = 80, 60


def line_words(
    line: ReadPart | ReadBranch,
    names: Mapping[str, str],
    waiting: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """One line as (its words, what it is), as the Discord card says it. A name the DM has not kept
    is said to be left out, as the keep leaves it out (`waiting`)."""

    def named(entity_id: str | None) -> str:
        name = names.get(entity_id or "", "")
        return f"{name} ({NOT_KEPT_SHORT})" if name and entity_id in waiting else name

    words = shorten(line.name if isinstance(line, ReadBranch) else line.words, LINE_WORDS_MAX)
    if isinstance(line, ReadBranch):
        what = f"{BRANCH_WORD}, {HINT_SAID}" if line.state == HINTED else BRANCH_WORD
        people = [f"{named(e)} ({part})" for part, e in line.parts if e in names]
        return words, what + (f" · {', '.join(people)}" if people else "")
    who = named(line.who_id)
    if line.kind == tp.HELP and line.state == "lent":
        what = f"Lent by {who}" if who else "Lent"
        terms = shorten(line.terms, TERMS_SHOWN)
        return words, what + (f", to return: {terms}" if terms else ", to give back")
    if line.kind == tp.REWARD_GIVEN:
        return words, (f"Given by {who}" if who else "Given") + ", not promised"
    what = KIND_WORDS[line.kind] + (f" from {who}" if who else "")
    if line.due:
        what += f", {DUE_SAID[line.due]}"
    if line.kind == tp.HELP:
        what += f", {STATE_SAID[line.state]}"
    if line.limit_kind:
        what += f", {LIMIT_SAID[line.limit_kind]}"
    return words, what
