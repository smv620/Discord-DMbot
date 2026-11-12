"""Fill in the Discord names of people DMbot only knows by number (#4225).

A person a campaign knows only by Discord id is called `Player 1234` (the last four digits) until a
name is known; the website's speaker pickers showed exactly that. This looks a member up by id with
`guild.fetch_member`, a plain request that needs no privileged members intent, and gives their
placeholder entry the name (`MemoryStore.name_person`), so the story graph and the pickers read it.
A few people a round, slowly; someone who has left the server is called "Someone who left the
server (…1234)" so the placeholder cannot be mistaken for a name. Counts only in the log, never a
name or an id.

The names of people who agreed to be recorded also go in `member_directory`
(`MemberDirectory.remember` still refuses anyone who did not agree, and purges them when they stop,
which this does not change); a name for someone who did not agree lives only in the campaign's own
entry, which the DM already sees.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import discord

from dmbot.memory.models import MemoryRuleError
from dmbot.memory.store import left_name

log = logging.getLogger(__name__)

PER_ROUND = 5  # people looked up in a server each round (a round is a minute)
RETRY_AFTER_S = 3600.0  # a person we could not look up is left alone this long
CANDIDATES = 50  # how many placeholders to read to find PER_ROUND not skipped


def shown_name(display_name: str, username: str) -> str:
    """ "Eric (eric_92)": the display name, with the username after it when they differ."""
    display, user = display_name.strip(), username.strip()
    if display and user and display.casefold() != user.casefold():
        return f"{display} ({user})"
    return display or user


async def run(
    bot: Any, guild_id: int, skip: dict[tuple[int, int], float], *, now: float | None = None
) -> int:
    """One round for one server: up to `PER_ROUND` placeholders named. `skip` remembers, in
    memory, who not to ask again for a while. Returns how many people were looked up."""
    memory = getattr(bot, "memory", None)
    guild = bot.get_guild(guild_id)
    if memory is None or guild is None:
        return 0
    stamp = time.monotonic() if now is None else now
    found = await memory.placeholder_people(guild_id, CANDIDATES)
    todo = [user for user, _ in found if skip.get((guild_id, user), 0.0) <= stamp][:PER_ROUND]
    named = left = failed = 0
    for user_id in todo:
        if getattr(bot, "_closing", False):
            break
        name: str | None = None
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
            if getattr(member, "bot", False):
                skip[(guild_id, user_id)] = stamp + RETRY_AFTER_S
                continue
            name = shown_name(member.display_name, member.name)
        except discord.NotFound:
            name = left_name(user_id)  # not in the server (any more)
            left += 1
        except Exception as exc:  # Discord could not say: ask again later, never stop the rest
            failed += 1
            skip[(guild_id, user_id)] = stamp + RETRY_AFTER_S
            log.info("A member lookup failed (%s)", type(exc).__name__)
            continue
        if not name:
            skip[(guild_id, user_id)] = stamp + RETRY_AFTER_S
            continue
        try:
            for campaign_id in await memory.placeholder_campaigns(guild_id, user_id):
                if await memory.name_person(guild_id, campaign_id, user_id, name):
                    named += 1
        except (MemoryRuleError, ValueError) as exc:
            skip[(guild_id, user_id)] = stamp + RETRY_AFTER_S
            log.info("A member's name was refused (%s)", type(exc).__name__)
            continue
        directory = getattr(bot, "members", None)
        if directory is not None and name != left_name(user_id):
            try:  # (only for someone who agreed: `remember` writes nothing for anyone else)
                await directory.remember(guild_id, user_id, member.display_name, member.name)
            except Exception as exc:
                log.info("A member's directory row failed (%s)", type(exc).__name__)
    log.info(
        "Member names: %d looked up, %d entries named, %d left the server, %d failed",
        len(todo), named, left, failed,
    )  # fmt: skip
    return len(todo)
