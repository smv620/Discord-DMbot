"""Which names a session's own play said, without the recap read aloud at its start (#3172 item d).

`memory_heard` is the importance rank's "Recurring" floor: a name heard in two separate sessions
(Supervisor's ruling, the owner's decision of 2026-11-01). A DM who opens a session by reading
last time's recap aloud says every name in it again, so before this every name in a recap counted
as heard twice, and the floor lost its meaning. The fix is in two halves (agreed with CloudDev-lead
on #3172, 2026-11-01): the bot keeps, for each (entity, speaker) count, the line numbers it came
from; this pure filter takes away the lines the session's split marks as a recap, and the bot
writes what is left. Pure: no database, no AI.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping


def drop_retold(
    counts: Mapping[tuple[str, int], Collection[int]], is_retold: Callable[[int], bool]
) -> dict[tuple[str, int], int]:
    """Each (entity id, speaker) key's count of lines, without the lines `is_retold` says were the
    recap read aloud. Line numbers are `Said.number`: 1-based places among the session's stored
    lines. The predicate should be the story reader's own (`extractor.recap_numbers`, which the bot
    gets from `_recap_lines`), so the two can't drift; `Split.used_as(n) == RECAP` is the same.
    A key left with no line is dropped, so a name only the recap said is not heard in this session
    at all. A line counted twice for one key counts once: it was said once. An unsure recap is not
    retold (`used_as` gives play for it), so a doubt never takes counts away."""
    kept: dict[tuple[str, int], int] = {}
    for key, lines in counts.items():
        left = {n for n in lines if not is_retold(n)}
        if left:
            kept[key] = len(left)
    return kept
