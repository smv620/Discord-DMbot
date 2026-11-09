"""The notice a lore upload leaves when it adds what DMbot is sure of by itself (#3878).

One live message per upload in the DM Bot channel (the DM screen is for live play, #3874), counts
only: how many names of each kind and how many connections were added, how many questions are
left, and the buttons: the Names page, the Story Graph, **↩️ Undo this upload** and, when
questions are left, a button for them. The same words go privately to the DMs. Never a name or a
word of the text; the upload's own name and date are the DM's, as they saw them.

Undo carries the server, campaign and notice in its id, so it works after a restart and in a
private message (which has no server). Only a DM of the campaign can press it, checked on every
press. It takes back exactly the changes the upload made, newest first, as the store's own Undo
does; one that something done since depends on is left, and the reply says so. It works for 7 days.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.memory import background as bg
from dmbot.memory.models import MemoryRuleError, TooLateToUndo
from dmbot.mentions import NO_PINGS
from dmbot.ui import dm_links
from dmbot.ui.dmbot_commands import _bot, _tell
from dmbot.upload_notices import Notice

log = logging.getLogger(__name__)

NOT_YOURS = "Only this campaign's DM can undo this."
GONE = "I can't find this upload any more, so nothing was changed."
ALREADY = "This upload was already taken back."
TOO_LATE = "It is too late to undo this upload (7 days). Use each name's card instead."
PARTLY = (
    "↩️ Took back {done} of {total} changes from this upload. The rest were changed since, so "
    "they stay."
)
COULD_NOT = "Couldn't undo this upload just now. Nothing was changed; press again."
STOPPED = "↩️ Took back {done} of {total}; press Undo on each name's card for the rest."
NOTHING_LEFT = "↩️ Took back this upload. Its names and connections are gone."

NAMES_KEYS = ("npc", "place", "faction", "item", "event")
UNDO_LABEL = "↩️ Undo this upload"


def _plural(kind: str, count: int) -> str:
    one, many = bg.LABELS.get(kind, (kind, kind + "s"))
    return f"{count} {one if count == 1 else many}"


def notice_text(label: str, counts: Mapping[str, int], questions: int) -> str:
    """ "📜 Added **62 names** (30 NPCs, 24 places) and **48 connections** from <label>." and, when
    something is left, "**3 questions** need you." Counts only."""
    kinds = {k: v for k, v in counts.items() if k not in ("connections", "other_names") and v}
    names = sum(kinds.values())
    parts = ", ".join(_plural(k, v) for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
    connections = counts.get("connections", 0)
    other = counts.get("other_names", 0)
    added: list[str] = []
    if names:
        added.append(f"**{names} name{'' if names == 1 else 's'}** ({parts})")
    if connections:
        added.append(f"**{connections} connection{'' if connections == 1 else 's'}**")
    if other:
        added.append(f"**{other} other name{'' if other == 1 else 's'}**")
    what = " and ".join(added) if added else "**nothing new**"
    source = f" from {discord.utils.escape_markdown(label)}" if label else ""
    text = f"📜 Added {what}{source}."
    if questions:
        text += (
            f"\n❓ **{questions} question{'' if questions == 1 else 's'}** "
            f"need{'s' if questions == 1 else ''} you."
        )
    return text


def notice_view(
    notice: Notice,
    *,
    site_url: str,
    now: int | None = None,
    asks: Sequence[discord.ui.Item[Any]] = (),
) -> discord.ui.View:
    """The buttons: the questions left, if any (`asks`, the same buttons the private question has,
    each a persistent one), Names and Story Graph (link buttons, when the site has an https
    address), and Undo while it is still possible."""
    view = discord.ui.View(timeout=None)
    for ask in asks:
        view.add_item(ask)
    names = dm_links.site_link(site_url, "names", notice.guild_id, notice.campaign_id)
    graph = dm_links.site_link(site_url, "web", notice.guild_id, notice.campaign_id)
    if names:
        view.add_item(discord.ui.Button(label="See them on the Names page", emoji="🌐", url=names))
    if graph:
        view.add_item(discord.ui.Button(label="See them on the Story Graph", emoji="🕸️", url=graph))
    if notice.can_undo(int(time.time()) if now is None else now):
        view.add_item(UndoUploadButton(notice.guild_id, notice.campaign_id, notice.id))
    return view


class UndoUploadButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:up:undo:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32}):(?P<id>[0-9a-f]{32})",
):
    def __init__(self, guild_id: int, campaign_id: str, notice_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=UNDO_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:up:undo:{guild_id}:{campaign_id}:{notice_id}",
            )
        )
        self.guild_id, self.campaign_id, self.notice_id = guild_id, campaign_id, notice_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoUploadButton:
        return cls(int(match["guild"]), match["campaign"], match["id"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        bot = _bot(interaction)
        campaign = await bot.campaigns.get(self.guild_id, self.campaign_id)
        if campaign is None or bot.memory is None or bot.upload_notices is None:
            await _tell(interaction, GONE)
            return
        if interaction.user.id not in campaign.dm_user_ids:  # checked on every press
            await _tell(interaction, NOT_YOURS)
            return
        notices = bot.upload_notices
        found = await notices.get(self.guild_id, self.notice_id)
        if found is None:
            await _tell(interaction, GONE)
            return
        if found.undone_at is not None:
            await _tell(interaction, ALREADY)
            return
        if not found.can_undo(int(time.time())):
            await _tell(interaction, TOO_LATE)
            return
        claimed = await notices.claim_undo(self.guild_id, self.notice_id)
        if claimed is None:  # (a second press at the same moment)
            await _tell(interaction, ALREADY)
            return
        await interaction.response.defer()
        done = 0
        broke = False
        for batch in reversed(claimed.changes):  # newest first, as one chain of Undos
            try:
                await bot.memory.undo(campaign.guild_id, campaign.id, batch)
                done += 1
            except (TooLateToUndo, MemoryRuleError):
                continue  # changed since: left as it is
            except Exception:  # something unexpected: stop here, say so, never lose the button
                log.exception("Undoing an upload stopped")
                broke = True
                break
        if bot.lookup is not None:
            bot.lookup.drop([campaign.id])
        total = len(claimed.changes)
        if broke and done == 0:
            await notices.give_back_undo(self.guild_id, self.notice_id)  # nothing changed: again
            await _tell(interaction, COULD_NOT)
            return
        if broke:
            text = STOPPED.format(done=done, total=total)
        else:
            text = NOTHING_LEFT if done == total else PARTLY.format(done=done, total=total)
        with_suppress = discord.HTTPException
        try:
            await interaction.edit_original_response(
                content=text, view=None, allowed_mentions=NO_PINGS
            )
        except with_suppress:
            await _tell(interaction, text)


async def add_sure(
    bot: Any, campaign: Any, stored: Mapping[str, Sequence[str]], label: str
) -> tuple[Notice, int] | None:
    """Add what DMbot is sure of from this reading, as the DM's own calls (`memory.auto_accept`),
    and keep the upload's notice with the changes it made. Returns the notice and the number of
    questions left (names and connections waiting, "Which one?" and same-person questions), or
    None when nothing could be kept (no store). Never raises."""
    from dmbot.memory import auto_accept as auto
    from dmbot.ui.names import background_kind

    try:
        memory, links, notices = bot.memory, bot.backstory_links, bot.upload_notices
        if memory is None or notices is None:
            return None
        guild, cid = campaign.guild_id, campaign.id
        ids = {i for group in stored.values() for i in group}
        tally = auto.Tally()
        await auto.accept_names(
            memory, guild, cid, tally, narrow=True, only=ids, kind_of=background_kind
        )
        await auto.accept_other_names(memory, guild, cid, tally, only_entries=ids)
        if links is not None:  # (a bot without the connections store adds the names only)
            await auto.accept_connections(memory, links, guild, cid, tally, upload=label)
        if bot.lookup is not None:
            bot.lookup.drop([cid])
        counts = {**tally.kinds}
        if tally.accepted[auto.CONNECTIONS]:
            counts["connections"] = tally.accepted[auto.CONNECTIONS]
        if tally.accepted[auto.OTHER_NAMES]:
            counts["other_names"] = tally.accepted[auto.OTHER_NAMES]
        questions = (
            tally.waiting[auto.NAMES]
            + tally.waiting[auto.CONNECTIONS]
            + tally.refused[auto.NAMES]
            + tally.refused[auto.CONNECTIONS]
            + (await links.count_which(guild, cid) if links is not None else 0)
            + len(await memory.same_person_offers(guild, cid))
        )
        notice = await notices.add(guild, cid, label, counts, tally.batches)
        return notice, questions
    except Exception:
        log.exception("Couldn't add what an upload was sure of")
        return None
