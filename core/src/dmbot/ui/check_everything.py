"""The "✅ Check everything" button and the walk it starts (#4192): the DM's waiting checks one
after another in a fixed order (names, who is who, what was said, connections), each card the
existing card of its kind, with the way between them the only new thing.

Nothing is kept about where the DM got to. Each part ends by reading what still waits (the bot's
own count) and moving on, and Carry on is the same button read again, so a restart loses nothing.
Counts and part names only; no name or word of the text goes in a message or a log line here.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot import walk as walks
from dmbot.dm_questions import CLAIMS, CONNECTIONS, KINDS, NAMES, PEOPLE
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _tell
from dmbot.walk import Walk

log = logging.getLogger(__name__)

WALK_ID = "dmbot:walk:"


class WalkButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:walk:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """On the after-session message when two or more kinds of checks wait. The server is in the
    id because a private chat has none; the label's count is only for show (the walk reads what
    waits when it is pressed)."""

    def __init__(
        self, guild_id: int, campaign_id: str, left: int = 0, *, started: bool = False
    ) -> None:
        label = walks.button_label(left, started=started) if left > 0 else "✅ Check everything"
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success,
                custom_id=f"{WALK_ID}{guild_id}:{campaign_id}",
            )
        )
        self.guild_id = guild_id
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WalkButton:
        return cls(int(match["guild"]), match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await begin(interaction, self.guild_id, self.campaign_id)


async def waiting_counts(
    interaction: discord.Interaction, guild_id: int, campaign_id: str
) -> dict[str, int]:
    """What waits now, by the walk's kinds, read again each time."""
    bot = _bot(interaction)
    found = await bot.count_waiting(guild_id, campaign_id)
    return {
        NAMES: found.names,
        PEOPLE: found.people,
        CLAIMS: found.claims,
        CONNECTIONS: found.connections,
        KINDS: await _count_kinds(bot, guild_id, campaign_id),
    }


async def _count_kinds(bot: Any, guild_id: int, campaign_id: str) -> int:
    """The kind-of-connection offers waiting (a count that cannot be read is zero; the log gets the
    error type only). Not part of `count_waiting`: that one is the upload message's four."""
    if bot.memory is None:
        return 0
    try:
        return len(await bot.memory.kind_offer_list(guild_id, campaign_id))
    except Exception as exc:
        log.warning("Check everything: couldn't count kinds (%s)", type(exc).__name__)
        return 0


async def begin(interaction: discord.Interaction, guild_id: int, campaign_id: str) -> None:
    """The button was pressed (first time or Carry on): open the first part that has something."""
    from dmbot.ui import names

    # Counting what waits takes several reads: answer Discord first (it gives up after 3 seconds).
    await _answer_first(interaction)
    campaign = await names._campaign_for(interaction, campaign_id)  # only whoever runs the campaign
    if campaign is None:
        return
    counts = await waiting_counts(interaction, campaign.guild_id, campaign.id)
    parts = walks.parts_for(counts)
    if not parts:
        await _tell(interaction, walks.NOTHING_TO_CHECK)
        return
    message = interaction.message
    origin = (message.channel.id, message.id) if message is not None else None
    walk = Walk(campaign.id, campaign.guild_id, interaction.user.id, parts, origin=origin)
    log.info("Check everything started (%s)", ", ".join(f"{k}={counts[k]}" for k in parts))
    await run_part(interaction, walk)


async def run_part(interaction: discord.Interaction, walk: Walk) -> None:
    """Open the walk's current part with that kind's own flow, handing it the walk."""
    kind = walk.kind
    if kind == NAMES:
        from dmbot.ui.names import start_review

        # (the names part opens with Add all when there are names to add in bulk, then goes on)
        await start_review(interaction, walk.campaign_id, bulk=True, walk=walk)
    elif kind == PEOPLE:
        from dmbot.ui.people_review import start_people_review

        await start_people_review(interaction, walk.campaign_id, walk=walk)
    elif kind == CLAIMS:
        from dmbot.ui.claims_review import start_claims_review

        await start_claims_review(interaction, walk.campaign_id, walk=walk)
    elif kind == KINDS:
        from dmbot.ui.kind_offer import start_walk_part

        await start_walk_part(interaction, walk.guild_id, walk.campaign_id, walk)
    else:
        from dmbot.ui.name_background import ask_connections

        await ask_connections(interaction, walk.campaign_id, walk=walk)


async def next_part(interaction: discord.Interaction, walk: Walk) -> None:
    """A part ended: read what still waits and open the next part that has something, with one
    line saying so; or say everything is checked."""
    counts = await waiting_counts(interaction, walk.guild_id, walk.campaign_id)
    later = walk
    while not later.last:
        later = later.next()
        if counts.get(later.kind, 0) > 0:
            await _tell(interaction, walks.between(walk.kind, later.kind, counts[later.kind]))
            await run_part(interaction, later)
            return
    # Anything left for later (Later, Skip the rest) still waits: don't say everything is checked.
    left = sum(counts.values())
    if left > 0:
        await _tell(interaction, walks.left_for_later(left))
        return
    await _tell(interaction, walks.ALL_DONE)


def progress(walk: Walk | None, done: int, total: int) -> str:
    """The progress line for a card, or nothing outside a walk."""
    return f"_{walk.progress(done, total)}_\n" if walk is not None else ""


def add_stop(view: _Menu, walk: Walk | None, row: int = 4) -> None:
    """Stop for now, on every card of a walk (row 4, the last, so no flow's own buttons move). It
    needs no DM check of its own: it only ends this private card and changes a button's label, and
    the walk was opened by `begin`, which checked the DM."""
    if walk is None:
        return

    async def stop(interaction: discord.Interaction) -> None:
        await stop_walk(interaction, walk, view)

    view.add_item(
        _Button(stop, label=walks.STOP_LABEL, style=discord.ButtonStyle.secondary, row=row)
    )


async def stop_walk(interaction: discord.Interaction, walk: Walk, view: _Menu) -> None:
    """Stop for now: end this card, and change the message's button to Carry on (N left)."""
    from dmbot.ui.dmbot_commands import _replace

    view.stop()
    await _replace(
        interaction, walks.STOPPED if walk.origin is not None else walks.STOPPED_NO_ORIGIN, None
    )
    counts = await waiting_counts(interaction, walk.guild_id, walk.campaign_id)
    await relabel_origin(interaction, walk, sum(counts.values()))


async def relabel_origin(interaction: discord.Interaction, walk: Walk, left: int) -> None:
    """Change only the walk button's label on the message the DM pressed, keeping every other
    button and link on it. Best effort: the log gets the error type only."""
    if walk.origin is None or left <= 0:
        return  # (nothing to carry on with, or no message to change: the button keeps its label)
    channel_id, message_id = walk.origin
    try:
        channel = await interaction.client.fetch_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        message = await channel.fetch_message(message_id)
        view = discord.ui.View.from_message(message, timeout=None)
        changed = False
        for item in view.children:
            if isinstance(item, discord.ui.Button) and (item.custom_id or "").startswith(WALK_ID):
                item.label = walks.button_label(left, started=True)
                changed = True
        if changed:
            with contextlib.suppress(discord.HTTPException):
                await message.edit(view=view)
    except Exception as exc:
        log.warning("Check everything: couldn't relabel the button (%s)", type(exc).__name__)
