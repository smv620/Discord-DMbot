"""Can the bot post where it needs to? Pure checks, testable without Discord.

Posting fails silently otherwise (the bot only logs a warning), so `/dmbot start`
checks up front and tells the DM exactly which permission to add.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord


@dataclass(frozen=True, slots=True)
class PostProblem:
    channel_id: int
    missing: tuple[str, ...]
    why: str
    in_thread: bool = False
    unseen: bool = False  # the bot can't find the channel at all


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


SAME_CHANNEL = (
    "Run `/dmbot start` from your private DM channel, not the voice channel's chat — "
    "players can read that."
)
STARTING_UP = "I'm still starting up. Try `/dmbot start` again in a moment."


def post_problems(
    *,
    screen_id: int,
    screen_perms: discord.Permissions | None,
    screen_in_thread: bool,
    voice_id: int,
    voice_perms: discord.Permissions,
) -> list[PostProblem]:
    """Problems posting DM updates (screen) and the recording notice (voice chat).

    `screen_perms` is None when the bot can't find the screen channel at all.
    """
    problems: list[PostProblem] = []
    if screen_perms is None:
        problems.append(PostProblem(screen_id, (), "", unseen=True))
    elif missing := missing_post_permissions(screen_perms, in_thread=screen_in_thread):
        problems.append(
            PostProblem(screen_id, tuple(missing), "your DM updates go here", screen_in_thread)
        )
    if missing := missing_post_permissions(voice_perms):
        problems.append(
            PostProblem(voice_id, tuple(missing), "players must see the recording notice there")
        )
    return problems


def _bullet(p: PostProblem) -> str:
    if p.unseen:
        return (
            f"• I can't see <#{p.channel_id}>. Give me **View Channel** there, or run "
            "`/dmbot start` from another private channel"
        )
    needs = " and ".join(f"**{name}**" for name in p.missing)
    where = ", set on its parent channel" if p.in_thread else ""
    return f"• <#{p.channel_id}> needs {needs}{where} ({p.why})"


def join_blocked_message(problems: list[PostProblem]) -> str:
    count = sum(len(p.missing) for p in problems if not p.unseen)
    it = "it" if count == 1 else "them"
    lines = ["⚠️ Not joining — I can't post where I need to:"]
    lines += [_bullet(p) for p in problems]
    if count:
        lines.append(
            f"Add {it} via Edit Channel → Permissions (for me or my role), "
            "then run `/dmbot start` again."
        )
    return "\n".join(lines)


def notice_failed_message(voice_channel_id: int) -> str:
    return (
        f"⚠️ Players weren't told I'm listening — the recording notice failed in "
        f"<#{voice_channel_id}>. Tell the table now. Then check I have **Send Messages** "
        "there; I'll retry on the next `/dmbot start`."
    )
