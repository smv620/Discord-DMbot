"""The rules and NPC channels DMbot no longer makes (#2874): delete the ones it made before.

`#dmb-rules-<short name>` (#1752) and `#dmb-npcs-<short name>` (#1761) were DMbot's own, read only
for everyone. The owner dropped them (2026-10-28): anyone can ask DMbot about the rules in the
chat, the DM has them in their menu, and the rules and the NPC pages are on the website. This
module is what is left of them: the names that say a channel is still DMbot's, and the one-time
clean-up of the ones that exist.

A channel is deleted only when its id is the one stored for the campaign **and** its name still
starts like DMbot's own (the trust rule `retention_channels` and the peek buttons use), so a
channel an admin renamed and reused is kept. Either way the stored id is cleared afterwards. A
failure (no Manage Channels, already gone) is counted and never stops anything. It needs the two
id columns until every server has run it; they are dropped in a later migration.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import discord

from dmbot.campaigns import Campaign, CampaignStore
from dmbot.dm_screen.names import PREFIX

log = logging.getLogger(__name__)

# (web/test/help.test.ts looks for the names "dmb-rules" and "dmb-npcs" in this file.)
RULES_PREFIX = f"{PREFIX}-rules-"
NPCS_PREFIX = f"{PREFIX}-npcs-"
REASON = "DMbot no longer uses this channel"  # the audit-log line
CALL_TIMEOUT_S = 15.0  # one slow Discord call must not hold up a start


def is_rules_name(name: str) -> bool:
    return name.startswith(RULES_PREFIX)


def is_npcs_name(name: str) -> bool:
    return name.startswith(NPCS_PREFIX)


@dataclass(slots=True)
class Retired:
    deleted: int = 0  # DMbot's own, deleted
    kept: int = 0  # not named like DMbot's, or not a text channel of this server: left alone
    failed: int = 0  # a delete Discord refused or that timed out


async def _retire_one(
    guild: discord.Guild, channel_id: int, named_right: Callable[[str], bool]
) -> str:
    """ "deleted", "kept" (left alone), "gone" (not there any more) or "failed"."""
    try:
        async with asyncio.timeout(CALL_TIMEOUT_S):
            found: object = guild.get_channel(channel_id)
            if found is None:
                found = await guild.fetch_channel(channel_id)
            if not isinstance(found, discord.TextChannel) or found.guild.id != guild.id:
                return "kept"
            if not named_right(found.name):
                return "kept"  # renamed and reused: not ours any more
            await found.delete(reason=REASON)
            return "deleted"
    except discord.NotFound:
        return "gone"
    except (discord.DiscordException, TimeoutError):
        return "failed"


async def remove_old_channels(
    guild: discord.Guild, campaign: Campaign, campaigns: CampaignStore
) -> Retired:
    """Delete this campaign's old rules and NPC channels if they are DMbot's own, and clear both
    stored ids. Never raises; logs counts only."""
    done = Retired()
    for channel_id, named_right, forget in (
        (campaign.rules_channel_id, is_rules_name, campaigns.set_rules_channel),
        (campaign.npcs_channel_id, is_npcs_name, campaigns.set_npcs_channel),
    ):
        if channel_id is None:
            continue
        outcome = await _retire_one(guild, channel_id, named_right)
        if outcome == "deleted":
            done.deleted += 1
        elif outcome == "kept":
            done.kept += 1
        elif outcome == "failed":
            done.failed += 1
        # (a channel that is gone, kept or failed is forgotten too: the id would only point at
        # something DMbot never touches again)
        with contextlib.suppress(Exception):
            await forget(guild.id, campaign.id, None)
    if done.deleted or done.kept or done.failed:
        log.info(
            "Old rules and NPC channels: %d deleted, %d kept, %d failed",
            done.deleted,
            done.kept,
            done.failed,
        )
    return done
