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
from dataclasses import dataclass, field, replace
from typing import Protocol

from dmbot.campaigns.models import Campaign
from dmbot.questions import counts as counting
from dmbot.questions import kinds, messages
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

    async def board(
        self,
        user_id: int,
        campaign: Campaign,
        found: Mapping[str, Counted],
        message_id: int | None,
        *,
        create: bool,
    ) -> tuple[int | None, str]:
        """Show the Questions message: edit the one with `message_id`, or (`create`) send a new one
        when there is none or it is gone. Returns its id and a link to it ("" if there is none)."""
        ...


@dataclass(slots=True)
class Round:
    sources: Sources
    counts: QuestionCounts
    notices: NoticeStore
    sender: Sender
    campaigns: Callable[[int], Awaitable[list[Campaign]]]
    clock: Callable[[], float] = time.time
    notify: bool = True  # False: count only (the notices are off, DMBOT_QUESTIONS=0)
    rounds: int = 0
    latest: dict[tuple[int, str], Mapping[str, Counted]] = field(default_factory=dict)

    async def run(self, guild_ids: Collection[int]) -> int:
        """One round over these servers. Returns how many messages were sent."""
        self.rounds += 1
        self.latest.clear()  # only this round's counts are kept
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
            if user_id in per_user:
                continue  # they get a new message below, which replaces the old one
            try:
                await self._drop(guild_id, user_id, campaign_ids)
            except Exception as exc:
                log.warning("Questions: taking a notice away failed (%s)", type(exc).__name__)
        for user_id, lines in per_user.items():
            try:
                sent += await self._tell(guild_id, user_id, lines)
            except Exception as exc:
                log.warning("Questions: a notice failed (%s)", type(exc).__name__)
        return sent

    async def _campaign(
        self,
        campaign: Campaign,
        per_user: dict[int, list[messages.Line]],
        clears: dict[int, list[str]],
    ) -> None:
        g, c = campaign.guild_id, campaign.id
        before = await self.counts.read(g, c)
        # The heavy kinds are counted on the first round and every 5th; in between they keep their
        # last number (a kind with no stored row yet waits for that round).
        only = None if self.rounds % HEAVY_EVERY == 1 or not before else set(before) - HEAVY
        now_counts = await counting.count_kinds(self.sources, g, c, only=only)
        merged: Mapping[str, Counted] = {**before, **now_counts}  # a kind that failed keeps its old
        self.latest[(g, c)] = merged
        changed = {k: v.n for k, v in merged.items() if k in kinds.SHOWN} != {
            k: v.n for k, v in before.items() if k in kinds.SHOWN
        }
        n = total(merged)
        was_answered, new_question = answered(before, merged), grew(before, merged)
        recording = await self.sender.recording(g, c) if self.notify else False
        now = int(self.clock())
        for user_id in campaign.dm_user_ids if self.notify else ():
            try:  # one DM's trouble never costs the others their notice
                await self._user(
                    campaign,
                    user_id,
                    n,
                    was_answered,
                    new_question,
                    recording,
                    now,
                    per_user,
                    clears,
                    changed,
                )
            except Exception as exc:
                log.warning("Questions: one DM's timer failed (%s)", type(exc).__name__)
        # Written last: a round that failed before here sees the same change again next time, so a
        # muted DM still hears of the new question.
        if now_counts:
            await self.counts.write(g, c, now_counts)

    async def _user(
        self,
        campaign: Campaign,
        user_id: int,
        n: int,
        was_answered: bool,
        new_question: bool,
        recording: bool,
        now: int,
        per_user: dict[int, list[messages.Line]],
        clears: dict[int, list[str]],
        changed: bool = False,
    ) -> None:
        g, c = campaign.guild_id, campaign.id
        stored = await self.notices.get(g, c, user_id)
        if changed and stored.message_id is not None:  # keep the list they already have up to date
            await self._show(user_id, campaign, stored.message_id, create=False)
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
            return  # another round got there first
        if action == "clear" and stored.reminder_id is not None:
            clears.setdefault(user_id, []).append(c)
        elif action in ("first", "remind"):
            per_user.setdefault(user_id, []).append(
                messages.Line(campaign.name, n, first=action == "first", id=c)
            )

    async def _show(
        self, user_id: int, campaign: Campaign, message_id: int | None, *, create: bool
    ) -> str:
        """Edit (or make) their Questions message for this campaign; the link to it, or ""."""
        found = self.latest.get((campaign.guild_id, campaign.id), {})
        new_id, link = await self.sender.board(user_id, campaign, found, message_id, create=create)
        if new_id != message_id:
            await self.notices.set_message(campaign.guild_id, campaign.id, user_id, new_id)
        return link

    async def _drop(self, guild_id: int, user_id: int, campaign_ids: list[str]) -> None:
        """Nothing waits any more in these campaigns: the notice still in their chat goes, unless
        another campaign of theirs that still waits is listed in that same message, which then
        stays until its next reminder."""
        mine = [c for c in await self.campaigns(guild_id) if user_id in c.dm_user_ids]
        held: dict[str, int | None] = {}
        for campaign in mine:
            held[campaign.id] = (await self.notices.get(guild_id, campaign.id, user_id)).reminder_id
        for campaign_id in campaign_ids:
            message_id = held.get(campaign_id)
            if message_id is None:
                continue
            shared = any(m == message_id for other, m in held.items() if other not in campaign_ids)
            if not shared:
                await self.sender.delete(user_id, message_id)
            await self.notices.set_reminder(guild_id, campaign_id, user_id, None)

    async def _tell(self, guild_id: int, user_id: int, lines: list[messages.Line]) -> int:
        """One message for this DM: the one before it is deleted first, so the chat never holds
        more than one. A message that could not be sent is not retried this round. A DM of
        campaigns in two servers gets one message for each server: the servers' data never mix.
        If a first notice could not be sent, the next one reads as a reminder; it still backs off,
        so nothing is repeated in a loop."""
        mine = [c for c in await self.campaigns(guild_id) if user_id in c.dm_user_ids]
        gone: set[int] = set()
        for campaign in mine:  # every old notice of theirs here, whichever campaign it was about
            stored = await self.notices.get(guild_id, campaign.id, user_id)
            if stored.reminder_id is not None:
                if stored.reminder_id not in gone:
                    gone.add(stored.reminder_id)
                    await self.sender.delete(user_id, stored.reminder_id)
                await self.notices.set_reminder(guild_id, campaign.id, user_id, None)
        linked: list[messages.Line] = []
        for line in lines:  # each campaign's list is made now if they have none, and linked to
            about = next((c for c in mine if c.id == line.id), None)
            stored = await self.notices.get(guild_id, line.id, user_id)
            link = ""
            if about is not None:
                try:
                    link = await self._show(user_id, about, stored.message_id, create=True)
                except Exception as exc:
                    log.warning("Questions: the list could not be shown (%s)", type(exc).__name__)
            linked.append(replace(line, link=link))
        sent_id = await self.sender.send(user_id, messages.notice(linked))
        if sent_id is None:
            return 0
        for line in lines:
            await self.notices.set_reminder(guild_id, line.id, user_id, sent_id)
        return 1
