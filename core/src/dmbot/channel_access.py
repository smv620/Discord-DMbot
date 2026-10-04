"""Can the bot post where it needs to? Pure checks, testable without Discord.

Posting fails silently otherwise (the bot only logs a warning), so `/table join`
checks up front and tells the DM exactly which permission to add.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord

FIX_HINT = "Fix it in each channel's settings → Permissions, then run `/table join` again."


@dataclass(frozen=True, slots=True)
class PostProblem:
    channel_id: int
    missing: tuple[str, ...]
    why: str


def missing_post_permissions(perms: discord.Permissions, *, in_thread: bool = False) -> list[str]:
    """Names (as Discord shows them) of the permissions needed to post that are missing."""
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if in_thread:
        if not perms.send_messages_in_threads:
            missing.append("Send Messages in Threads")
    elif not perms.send_messages:
        missing.append("Send Messages")
    return missing


def join_blocked_message(problems: list[PostProblem]) -> str:
    lines = ["I can't start yet:"]
    for p in problems:
        needs = " and ".join(f"**{name}**" for name in p.missing)
        lines.append(f"• <#{p.channel_id}> needs {needs} — {p.why}")
    lines.append(FIX_HINT)
    return "\n".join(lines)


def notice_failed_message(voice_channel_id: int) -> str:
    return (
        f"⚠️ I couldn't post the recording notice in <#{voice_channel_id}>, so players may "
        "not know I'm listening. Give DMbot **Send Messages** there, or tell the table "
        "yourself."
    )
