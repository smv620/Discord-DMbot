"""The "Questions for you" round (#3435): count what waits, write the counts, and tell each DM.

Once a minute, for each campaign: count every kind (`counts`), compare with the last counts
(`store.answered`, `store.grew`), step each DM's timer (`timer.next_step`) and send what it says:
a first notice, or a reminder that replaces the last one. Sending goes through `Sender`, so the
round is tested without Discord. Counts, times and campaign names only; the log gets counts and
the error type, never a name or a question. A player is never a recipient: only the campaign's
DMs. Safe across restarts and a second copy: the timer is moved in the database before anything
is sent, and only the call that moved it sends.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from dmbot.campaigns.models import Campaign
from dmbot.questions import counts as counting
from dmbot.questions import messages
from dmbot.questions.counts import Counted, Sources
from dmbot.questions.notices import NoticeStore
from dmbot.questions.store import QuestionCounts, answered, grew, total
from dmbot.questions.timer import next_step

log = logging.getLogger(__name__)

ROUND_S = 60
HEAVY = frozenset({"lookalikes", "connections"})  # worked out in Python: counted every 5th round
HEAVY_EVERY = 5


class Sender(Protocol):
    """What the round needs from Discord."""

    async def recording(self, guild_id: int, campaign_id: str) -> bool: ...

    async def send(self, user_id: int, text: str) -> int | None:
        """A private message with the 🔕 Not now button; its id, or None if it could not be sent."""
        ...

    async def delete(self, user_id: int, message_id: int) -> None: ...


@dataclass(slots=True)
class Round:
    sources: Sources
    counts: QuestionCounts
    notices: NoticeStore
    sender: Sender
    campaigns: Callable[[int], Awaitable[list[Campaign]]]
    clock: Callable[[], float] = time.time
    rounds: int = 0

    async def run(self, guild_ids: Collection[int]) -> int:
        """One round over these servers. Returns how many messages were sent."""
        self.rounds += 1
        sent = 0
        for guild_id in guild_ids:
            try:
                sent += await self._guild(guild_id)
            except Exception as exc:
                log.warning("Questions: a server's round failed (%s)", type(exc).__name__)
        return sent

    async def _guild(self, guild_id: int) -> int:
        # Per DM: the lines of every campaign that has something to say, so one message goes out.
        per_user: dict[int, list[messages.Line]] = {}
        clears: dict[int, list[str]] = {}
        for campaign in await self.campaigns(guild_id):
            if campaign.paused or not campaign.dm_user_ids:
                continue
            try:
                await self._campaign(campaign, per_user, clears)
            except Exception as exc:
                log.warning("Questions: a campaign's round failed (%s)", type(exc).__name__)
        sent = 0
        for user_id, campaign_ids in clears.items():
            if user_id not in per_user:  # nothing else to say: take the old notice away
                await self._drop(guild_id, user_id, campaign_ids)
        for user_id, lines in per_user.items():
            sent += await self._tell(guild_id, user_id, lines)
        return sent

    async def _campaign(
        self,
        campaign: Campaign,
        per_user: dict[int, list[messages.Line]],
        clears: dict[int, list[str]],
    ) -> None:
        g, c = campaign.guild_id, campaign.id
        before = await self.counts.read(g, c)
        only = None if self.rounds % HEAVY_EVERY == 1 or not before else set(before) - HEAVY
        now_counts = await counting.count_kinds(self.sources, g, c, only=only)
        merged: Mapping[str, Counted] = {**before, **now_counts}  # a kind that failed keeps its old
        if now_counts:
            await self.counts.write(g, c, now_counts)
        n = total(merged)
        was_answered, new_question = answered(before, merged), grew(before, merged)
        recording = await self.sender.recording(g, c)
        now = int(self.clock())
        for user_id in campaign.dm_user_ids:
            stored = await self.notices.get(g, c, user_id)
            new, action = next_step(
                stored.notice,
                total=n,
                answered=was_answered,
                grew=new_question,
                recording=recording,
                now=now,
            )
            if new != stored.notice and not await self.notices.change(
                g, c, user_id, stored.notice, new
            ):
                continue  # another round got there first
            if action == "clear" and stored.reminder_id is not None:
                clears.setdefault(user_id, []).append(c)
            elif action in ("first", "remind"):
                per_user.setdefault(user_id, []).append(
                    messages.Line(campaign.name, n, first=action == "first", id=c)
                )

    async def _drop(self, guild_id: int, user_id: int, campaign_ids: list[str]) -> None:
        """Nothing waits any more: the notice or reminder still in their chat goes."""
        for campaign_id in campaign_ids:
            stored = await self.notices.get(guild_id, campaign_id, user_id)
            if stored.reminder_id is not None:
                await self.sender.delete(user_id, stored.reminder_id)
                await self.notices.set_reminder(guild_id, campaign_id, user_id, None)

    async def _tell(self, guild_id: int, user_id: int, lines: list[messages.Line]) -> int:
        """One message for this DM: the one before it is deleted first, so the chat never holds
        more than one. A message that could not be sent is not retried this round."""
        mine = [c for c in await self.campaigns(guild_id) if user_id in c.dm_user_ids]
        gone: set[int] = set()
        for campaign in mine:  # every old notice of theirs here, whichever campaign it was about
            stored = await self.notices.get(guild_id, campaign.id, user_id)
            if stored.reminder_id is not None:
                if stored.reminder_id not in gone:
                    gone.add(stored.reminder_id)
                    await self.sender.delete(user_id, stored.reminder_id)
                await self.notices.set_reminder(guild_id, campaign.id, user_id, None)
        sent_id = await self.sender.send(user_id, messages.notice(lines))
        if sent_id is None:
            return 0
        for line in lines:
            await self.notices.set_reminder(guild_id, line.id, user_id, sent_id)
        return 1
