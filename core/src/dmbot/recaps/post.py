"""How a recap reaches the table (#1382): the text the players read, split into messages Discord
accepts. Pure; the sending (and every check before it) is the bot's (`DMbot.post_recaps`).

The table reads the heading, the story without its cues, the housekeeping and a line that says the
DM approved it: a recap is a story told in a rule-bound world, not a ruling.
"""

from __future__ import annotations

from dmbot.discord_limits import MESSAGE_MAX as _MESSAGE_MAX
from dmbot.recaps.draft import heading
from dmbot.recaps.store import Job
from dmbot.recaps.text import strip_cues

PART_MAX = _MESSAGE_MAX - 100  # room for the "(1/3)" part marker added when it is posted


def to_the_table(job: Job, *, footer: bool = True) -> str:
    """What the players read. With `footer` False it is the same words without the line that says
    the DM posted it, for the DM reading it on the DM screen first (#1455)."""
    parts = [heading(job.title, job.game_day), "", strip_cues(job.summary)]
    if job.housekeeping:
        parts += [
            "",
            "**Numbers and items** (hit points, items, conditions)",
            *(f"- {b}" for b in job.housekeeping),
        ]
    if not footer:
        return "\n".join(parts)
    return "\n".join([*parts, "", "📜 _Recap of last session, posted by your DM._"])


def parts_of(text: str) -> list[str]:
    """The text in messages Discord accepts, cut at line ends."""
    out: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > PART_MAX:
            if current:
                out.append(current)
                current = ""
            out.append(line[:PART_MAX])
            line = line[PART_MAX:]
        if len(current) + len(line) + 1 > PART_MAX and current:
            out.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        out.append(current)
    return out or [""]
