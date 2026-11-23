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

from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dmbot.transcript.models import Line


def countable(lines: Sequence[Line], agreed: Collection[int]) -> Iterator[tuple[int, Line]]:
    """The lines the live count looks at, with their numbers (`Said.number`'s: the 1-based place
    among ALL the session's stored lines): from a speaker who agrees, never a DM sidebar or
    table-question line, never a blank one. The one filter the recount and the card's "first met"
    and "last mentioned" share, so they cannot drift from the count (#3172, #5050)."""
    for number, line in enumerate(lines, start=1):
        if line.sidebar or not line.text.strip() or line.user_id not in agreed:
            continue
        yield number, line


def count_lines(
    lines: Sequence[Line],
    agreed: Collection[int],
    named_in: Callable[[str], Iterable[str]],
    retold: Collection[int] = (),
) -> dict[tuple[str, int], int]:
    """The live count again, from a session's stored lines (#3172 item d, the correction pass):
    for each (entity id, speaker), the lines of that speaker which name the entity, once for each
    line, without the lines `retold` marks as last time's recap. The same lines the live count saw:
    a speaker who still agrees (`agreed`), never a DM sidebar or table-question line, never a
    blank one; the off-topic filter is NOT applied, because the live count did not apply it.
    Numbering is `Said.number`'s: the 1-based place among all the session's stored lines, so
    `retold` (`extractor.recap_numbers`) lines up. `named_in` is the live count's own matcher
    (`scene.mentions` over the campaign's lookup). Pure."""
    seen: dict[tuple[str, int], set[int]] = defaultdict(set)
    for number, line in countable(lines, agreed):
        for entity_id in set(named_in(line.text)):
            seen[entity_id, line.user_id].add(number)
    return drop_retold(seen, set(retold).__contains__)


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
