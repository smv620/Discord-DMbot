"""A DM's remembered players in Discord's player pickers (#4724, the Discord half of #4226).

Discord's own user picker can't be reordered or marked, so where a DM picks a player a second menu
sits above it: the players they have played with before, marked "played with you before" (the
website's words), each one a person who is a member of this server now. Choosing one does exactly
what picking them in Discord's picker does.

The list is the picking DM's own (`dmbot_web_dm_players_list`, read as that DM through their own
row-level-security view, so another DM's list can never come back), checked against the server's
current members when it is listed and again when one is picked. Names shown are the member's
current Discord name, never the stored one. Bots are never listed. Behind `DMBOT_REMEMBERED_PLAYERS`
(off): off, the pickers are exactly what they were.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from typing import Any

import discord

from dmbot.db import Database

log = logging.getLogger(__name__)

MARK = "played with you before"  # the website's words (wizard-words.ts, import-words.ts)
PLACEHOLDER = "Players you have played with before…"
MAX_OPTIONS = 25  # what a menu holds
GONE = "They aren't in this server any more. Pick them in the other list."

Pick = Callable[[discord.Interaction, "discord.User | discord.Member"], Awaitable[None]]


async def remembered_ids(db: Database, dm_user_id: int) -> list[int]:
    """The DM's remembered players' ids, the latest first: the DM's own list, read as that DM."""
    async with db.user(dm_user_id) as conn:
        cur = await conn.execute("SELECT dmbot_web_dm_players_list() AS r")
        row = await cur.fetchone()
    if row is None or not isinstance(row["r"], list):
        return []
    return [int(r["player"]) for r in row["r"] if str(r.get("player", "")).isdigit()]


CANDIDATES = 50  # the most of the list looked up (a DM's list can be long; a menu holds 25)
LOOKUPS_AT_ONCE = 5
LOOK_S = 2.0  # the whole lookup's time: a slow server costs the list, never Discord's 3 seconds


async def _member_now(guild: discord.Guild, player: int) -> discord.Member | None:
    """`player` as a member of `guild` now, or None. DMbot has no members intent, so the cache holds
    only people it has seen lately: a person in the server who is quiet has to be asked of Discord
    (`fetch_member`, which needs no privileged intent). Someone who isn't there, or a failed
    lookup, is None."""
    cached = guild.get_member(player)
    if cached is not None:
        return cached
    try:
        return await guild.fetch_member(player)
    except discord.HTTPException:  # (not here any more, or Discord said no: either way, skip)
        return None


async def members_of(
    guild: discord.Guild,
    ids: list[int],
    dm_user_id: int,
    *,
    only: Collection[int] | None = None,
) -> list[discord.Member]:
    """Those of `ids` who are members of `guild` now, in the list's order; never a bot, never the
    DM themselves, only those in `only` when it is given, at most one menu's worth. Looked up a few
    at a time, within `LOOK_S`; a lookup that runs out of time gives none (the picker works)."""
    wanted = [p for p in ids if p != dm_user_id and (only is None or p in only)][:CANDIDATES]
    gate = asyncio.Semaphore(LOOKUPS_AT_ONCE)

    async def one(player: int) -> discord.Member | None:
        async with gate:
            return await _member_now(guild, player)

    try:
        found = await asyncio.wait_for(asyncio.gather(*(one(p) for p in wanted)), LOOK_S)
    except TimeoutError:
        log.info("Remembered players: the lookup took too long, none listed")
        return []
    return [m for m in found if m is not None and not m.bot][:MAX_OPTIONS]


class RememberedSelect(discord.ui.Select[Any]):
    def __init__(self, guild: discord.Guild, members: list[discord.Member], on_pick: Pick) -> None:
        super().__init__(
            placeholder=PLACEHOLDER,
            options=[
                discord.SelectOption(label=m.display_name[:100], value=str(m.id), description=MARK)
                for m in members
            ],
        )
        self.guild, self.on_pick = guild, on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        member = await _member_now(self.guild, int(self.values[0]))  # (still here? asked again)
        if member is None or member.bot:
            await interaction.response.send_message(GONE, ephemeral=True)
            return
        await self.on_pick(interaction, member)


async def add_remembered(
    interaction: discord.Interaction,
    view: discord.ui.View,
    on_pick: Pick,
    guild_id: int,
    *,
    only: Collection[int] | None = None,
) -> None:
    """Put the DM's remembered players above the other items of `view`, when the switch is on and
    one of them is in the server now (and in `only`, when the picker takes only some people).
    Never raises: the picker works without it."""
    try:
        client: Any = interaction.client
        if not client.settings.remembered_players_on:
            return
        guild = client.get_guild(guild_id)
        if guild is None:
            return
        ids = await remembered_ids(client.campaigns.db, interaction.user.id)
        members = await members_of(guild, ids, interaction.user.id, only=only)
        if not members:
            return
    except Exception as exc:  # optional: the type, never a name
        log.warning("Remembered players: not listed (%s)", type(exc).__name__)
        return
    others = list(view.children)
    view.clear_items()
    try:
        view.add_item(RememberedSelect(guild, members, on_pick))
        for item in others:
            view.add_item(item)
    except Exception as exc:  # (a menu with no room: the picker stays as it was)
        log.warning("Remembered players: no room for the list (%s)", type(exc).__name__)
        view.clear_items()
        for item in others:
            view.add_item(item)
