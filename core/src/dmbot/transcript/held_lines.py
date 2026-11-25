"""The lines a live session's name scan reads, rebuilt from the stored transcript (#4905 slice 4c).

At a session's end the bot's name step reads `table.heard`: what the live path kept in memory for
it. The worker has no `Table`, so it rebuilds the same list from the stored lines. The rule is the
live path's own (`DMBot` where it appends to `table.heard`): a spoken line (cleaned) or a typed
one, from someone recorded now, in the order it began; never the DM's sidebar, a picture's
description or a line the topic filter hid as off-topic; the same cap. A line whose speaker has
stopped is left out by who said it, never by its words. Pure: no database, no Discord.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from dmbot.transcript.models import Line
from dmbot.transcript.topics import OFF_TOPIC

HEARD_MAX = 20_000  # lines kept for the after-session name scan (the bot's own limit)


def held_lines(
    lines: Sequence[Line], agreed: Collection[int], *, limit: int = HEARD_MAX
) -> list[tuple[int, str]]:
    """Each line as (who said it, its words), for the name scan, from the stored `lines`."""
    out: list[tuple[int, str]] = []
    for line in lines:
        if len(out) >= limit:
            break
        if (
            line.user_id in agreed
            and not line.sidebar
            and not line.image
            and line.topic != OFF_TOPIC
            and line.text.strip()
        ):
            out.append((line.user_id, line.text))
    return out
