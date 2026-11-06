"""The words of the "Questions for you" notices (#3435). Counts and campaign names only, never a
name from a campaign's story or the words of a question."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from dmbot.discord_limits import fit_message

NOT_NOW = "🔕 Not now"
NOT_NOW_DONE = "👍 No more reminders until there is a new question."
NOT_NOW_FAILED = "That didn't save. Press 🔕 Not now again in a moment."
NAME_MAX = 60


@dataclass(frozen=True, slots=True)
class Line:
    """One campaign in a notice: its name and how many questions wait."""

    campaign: str
    total: int
    first: bool  # the DM is hearing of these questions for the first time
    id: str = ""  # the campaign's id, for the caller (never shown)
    link: str = ""  # the Questions message in their private chat, when there is one


def _name(text: str) -> str:
    cut = text if len(text) <= NAME_MAX else text[: NAME_MAX - 1] + "…"
    return f"**{discord.utils.escape_markdown(discord.utils.escape_mentions(cut))}**"


def _questions(n: int) -> str:
    return f"{n} question" if n == 1 else f"{n} questions"


def _open(link: str) -> str:
    return f" [Open the list]({link})" if link else ""


def notice(lines: Sequence[Line]) -> str:
    """One private message for a DM. One campaign: a first notice says DMbot has a question (or
    how many) about it, a reminder says it is still waiting; both link to the Questions message.
    Several campaigns share one message, one line each, so a DM of many campaigns is never sent a
    stack of them."""
    if not lines:
        return ""
    if len(lines) == 1:
        one = lines[0]
        if one.first:
            what = "a question" if one.total == 1 else _questions(one.total)
            return f"❓ DMbot has {what} for you about {_name(one.campaign)}.{_open(one.link)}"
        waiting = f"❓ Still waiting: {_questions(one.total)} about {_name(one.campaign)}."
        return waiting + _open(one.link)
    head = "❓ DMbot has questions for you:" if any(x.first for x in lines) else "❓ Still waiting:"
    body = "\n".join(f"• {_name(x.campaign)}: {_questions(x.total)}{_open(x.link)}" for x in lines)
    return fit_message(f"{head}\n{body}")
