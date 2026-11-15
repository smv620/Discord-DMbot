"""A story thread's time limit, told to the DM when the game clock reaches it (quests slice 4b;
design `docs/design/quests.md` §8.4; Supervisor's ruling on #4448).

After every move of the game clock DMbot makes (`after_clock`, called by every clock-moving path:
CI checks `ClockStore.change_named`'s and `rejoin`'s callers), each dated, running limit of a live
thread is checked against the group furthest ahead. Reaching its "soon" mark or passing it says,
once each, one private message to each DM of the campaign, with one **Open** button to the thread
(the owner's rule for the DM's attention: a private message, one button at most, never a post on
the DM screen). ⏰ for time limits, by Supervisor's ruling. A passed limit changes nothing by
itself: the message says so. Live alerts are Discord's alone (the parity rule).

Never raises: a clock move must never fail for a message. Counts only in the log.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import discord

from dmbot.memory import time_limits as tl

log = logging.getLogger(__name__)

OPEN = "Open"
PASSED_TAIL = "Nothing has changed: your call."


def message(thread: str, words: str, left: str, stage: str) -> str:
    """ "⏰ Hlin's job: 1 day left (due Day 4). before the new moon" — the thread's name, the
    countdown in the cards' own words, and the limit's words, so the DM knows which one."""
    head = f"⏰ **{_plain(thread)}**: {left}."
    tail = f" {PASSED_TAIL}" if stage == tl.PASSED else ""
    return f"{head}{tail}\n> {_plain(words)}"


def _plain(text: str) -> str:
    """Names and words inside the message's own bold: nothing in them opens markdown or a link."""
    return "".join("\\" + ch if ch in "*_~|`>[]()<\\" else ch for ch in " ".join(text.split()))


class OpenThread(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:limit:open:(?P<campaign>[0-9a-f]{32}):(?P<thread>[0-9a-f]{32})",
):
    """The message's one button: the thread's card. Works after a restart; the card checks that
    whoever presses is a DM of the campaign, every time."""

    def __init__(self, campaign_id: str, thread_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=OPEN,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:limit:open:{campaign_id}:{thread_id}",
            )
        )
        self.campaign_id, self.thread_id = campaign_id, thread_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> OpenThread:
        return cls(match["campaign"], match["thread"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        from dmbot.ui.threads import show_thread

        await show_thread(interaction, self.campaign_id, self.thread_id)


async def after_clock(client: Any, guild_id: int, campaign_id: str) -> int:
    """Check the campaign's time limits against the clock now and tell the DMs what it reached
    (§8.4). Called after every clock move; how many messages were said. Never raises."""
    try:
        memory, clocks = getattr(client, "memory", None), getattr(client, "clocks", None)
        if memory is None or clocks is None:
            return 0
        clock = await tl.clock_now(clocks, guild_id, campaign_id)
        if clock is None:
            return 0
        now, calendar = clock
        due = await memory.time_limits_to_tell(guild_id, campaign_id, now, at=int(time.time()))
        if not due:
            return 0
        campaign = await client.campaigns.get(guild_id, campaign_id)
        dms = sorted(campaign.dm_user_ids) if campaign is not None else []
        said = 0
        for thread_id, name, part, stage in due:
            assert part.due_minute is not None  # (only dated limits are told)
            text = message(name, part.words, tl.left_said(part.due_minute, now, calendar), stage)
            for user_id in dms:
                view = discord.ui.View(timeout=None)
                view.add_item(OpenThread(campaign_id, thread_id))
                if await client._dm_message(user_id, text, view) is not None:
                    said += 1
        log.info("Time limits: %d reached, %d message(s) said", len(due), said)
        return said
    except Exception:
        log.warning("Couldn't check the time limits after a clock move", exc_info=True)
        return 0
