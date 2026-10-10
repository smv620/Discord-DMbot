"""The house-rules file, brought together with DMbot's copy (#969).

At every session start, if the campaign has a linked file, DMbot reads it (`dmbot.fetch`
checks every address) and compares it with the campaign's house rules by number. If
anything differs it posts one short note on the DM screen with three buttons: **Accept all**,
**Review** (one at a time: Accept or Skip) and **Ignore until the file changes**. Nothing
changes unless a DM presses one; every press checks again that the person is one of the
campaign's DMs, and the store checks again for each change. An uploaded file is compared the
same way, privately.

A file with no readable rule is never compared (it would offer to remove every rule): the DM
is told instead. The link is never shown (only the site it is on) and never logged.

What is waiting for a press lives in memory (`Pendings`, at most `MAX_PENDING`); after a
restart an old button says so. Register with `bot.add_dynamic_items(HouseSyncButton)`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import discord

from dmbot import fetch
from dmbot.campaigns import Campaign
from dmbot.rules import house_file, house_file_sync
from dmbot.rules.house_file_sync import Applied, Item

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
MAX_PENDING = 20
TEXT_MAX = 1900
READ_MAX_BYTES = 4 * house_file.MAX_FILE_CHARS  # as bytes: UTF-8 is up to 4 per character

CLOSED = (
    "This offer has ended (DMbot restarted, or it was already dealt with). To compare your "
    "file again, press **House-rules file** in `/dmbot houserules`."
)
ONLY_DMS = "Only this campaign's DMs can use these buttons."
FAILED = "Couldn't do that. Nothing was changed. Press the button again."
STALE = "That one was already dealt with."
NOTHING_CHANGES = "_Nothing changes unless you press a button._"
SAME = "Your house-rules file and DMbot's house rules match. Nothing to change."
NOT_TEXT = (
    "Your house-rules file isn't plain text. Share it as a Google Doc or a .txt file. "
    "Nothing was changed."
)
TOO_BIG = "That file is too big to be a house-rules file. Nothing was changed."
LABELS = {
    "all": ("Accept all", "✅", discord.ButtonStyle.primary),
    "review": ("Review", "🔍", discord.ButtonStyle.secondary),
    "ignore": ("Ignore until the file changes", "🙈", discord.ButtonStyle.secondary),
    "dismiss": ("Ignore", "🙈", discord.ButtonStyle.secondary),
    "accept": ("Accept", "✅", discord.ButtonStyle.primary),
    "skip": ("Skip", "⏭️", discord.ButtonStyle.secondary),
    "rest": ("Accept the rest", "✅", discord.ButtonStyle.secondary),
}

Send = Callable[[str, discord.ui.View], Awaitable[bool]]


def couldnt_read(why: str) -> str:
    return (
        f"I couldn't read your house-rules file: {why} The session goes on with DMbot's "
        "copy. Nothing was changed."
    )[:TEXT_MAX]


def no_rules(unreadable: int) -> str:
    extra = f" ({unreadable} lines couldn't be read)" if unreadable else ""
    return (
        f"I couldn't find any house rules in your file{extra}, so nothing was changed. A rule "
        "is one line: its number, a dot, then the rule, like `12. Potions are a bonus action`."
    )


@dataclass(slots=True)
class Pending:
    guild_id: int
    campaign_id: str
    items: list[Item]
    fingerprint: str
    summary: str
    from_link: bool  # a linked file (can be ignored until it changes) or an upload
    index: int = 0  # the item a review is on
    applied: Applied = field(default_factory=Applied)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Pendings:
    """What is waiting for a DM's press, newest last; the oldest is forgotten past the
    limit (its buttons then say the offer has ended)."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, Pending] = OrderedDict()

    def add(self, pending: Pending) -> str:
        key = uuid.uuid4().hex[:8]
        self._items[key] = pending
        while len(self._items) > MAX_PENDING:
            self._items.popitem(last=False)
        return key

    def get(self, key: str) -> Pending | None:
        return self._items.get(key)

    def drop(self, key: str) -> None:
        self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def offer_text(pending: Pending) -> str:
    return f"📄 {pending.summary}\n{NOTHING_CHANGES}"


def review_text(pending: Pending) -> str:
    item = pending.items[pending.index]
    head = f"📄 **Review {pending.index + 1} of {len(pending.items)}**\n"
    return (head + _md(house_file_sync.describe(item)))[:TEXT_MAX]


def offer_view(guild_id: int, key: str, pending: Pending) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("all", "review", "ignore" if pending.from_link else "dismiss"):
        view.add_item(HouseSyncButton(guild_id, key, action, 0))
    return view


def review_view(guild_id: int, key: str, pending: Pending) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    actions = ["accept", "skip"] + (["rest"] if pending.index + 1 < len(pending.items) else [])
    for action in actions:
        view.add_item(HouseSyncButton(guild_id, key, action, pending.index))
    return view


class HouseSyncButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        r"dmbot:hrs:(?P<action>all|review|ignore|dismiss|accept|skip|rest):"
        r"(?P<guild>[0-9]{1,20}):(?P<key>[0-9a-f]{8}):(?P<n>[0-9]{1,4})"
    ),
):
    def __init__(self, guild_id: int, key: str, action: str, n: int) -> None:
        label, emoji, style = LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=f"dmbot:hrs:{action}:{guild_id}:{key}:{n}",
            )
        )
        self.guild_id, self.key, self.action, self.n = guild_id, key, action, n

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HouseSyncButton:
        return cls(int(match["guild"]), match["key"], match["action"], int(match["n"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.guild_id, self.key, self.action, self.n)
        except Exception:
            log.exception("A house-rules file button failed")
            if interaction.response.is_done():
                await interaction.followup.send(FAILED, ephemeral=True)
            else:
                await interaction.response.send_message(FAILED, ephemeral=True)


async def _show(interaction: discord.Interaction, text: str, view: discord.ui.View | None) -> None:
    await interaction.response.edit_message(content=text, view=view, allowed_mentions=NO_PINGS)


async def _finish(interaction: discord.Interaction, key: str, pending: Pending) -> None:
    """The last word on an offer: what was done, and the buttons gone."""
    bot: Any = interaction.client
    bot.house_syncs.drop(key)
    result = house_file_sync.result_text(pending.applied)
    await _show(interaction, f"📄 {pending.summary}\n{_md(result)}"[:TEXT_MAX], None)
    if pending.applied.done:  # the file as it is now, privately (#969, part 1)
        from dmbot.ui import house_file as file_ui

        campaign = await bot.campaigns.get(pending.guild_id, pending.campaign_id)
        if campaign is not None:
            await file_ui.send_after_change(interaction, campaign, "House rules updated.")


async def press(
    interaction: discord.Interaction, guild_id: int, key: str, action: str, n: int
) -> None:
    """What a button does. The offer, the campaign and the person are checked every time."""
    bot: Any = interaction.client
    pending: Pending | None = bot.house_syncs.get(key) if interaction.guild_id == guild_id else None
    if pending is None or pending.guild_id != guild_id:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    campaign = await bot.campaigns.get(guild_id, pending.campaign_id)
    if campaign is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return
    store = bot.house_rules
    if store is None:
        await interaction.response.send_message(FAILED, ephemeral=True)
        return
    user_id = interaction.user.id
    async with pending.lock:  # two DMs pressing at once take turns
        if bot.house_syncs.get(key) is not pending:
            await interaction.response.send_message(CLOSED, ephemeral=True)
            return
        if action in ("accept", "skip", "rest") and n != pending.index:
            await interaction.response.send_message(STALE, ephemeral=True)
            return
        if action == "review":
            await _show(interaction, review_text(pending), review_view(guild_id, key, pending))
        elif action in ("dismiss", "ignore"):
            if action == "ignore":
                links = bot.house_file_links
                if links is not None:
                    await links.ignore(guild_id, pending.campaign_id, user_id, pending.fingerprint)
            bot.house_syncs.drop(key)
            await _show(interaction, f"📄 {pending.summary}\n_Ignored._", None)
        elif action in ("all", "rest"):
            todo = pending.items[pending.index :]
            done = await house_file_sync.apply(store, guild_id, pending.campaign_id, user_id, todo)
            pending.applied.done += done.done
            pending.applied.skipped += done.skipped
            await _finish(interaction, key, pending)
        else:  # "accept" or "skip": the item under review
            if action == "accept":
                one = [pending.items[pending.index]]
                done = await house_file_sync.apply(
                    store, guild_id, pending.campaign_id, user_id, one
                )
                pending.applied.done += done.done
                pending.applied.skipped += done.skipped
            pending.index += 1
            if pending.index >= len(pending.items):
                await _finish(interaction, key, pending)
            else:
                await _show(interaction, review_text(pending), review_view(guild_id, key, pending))


# ---- comparing -----------------------------------------------------------------------


async def compare_text(
    bot: Any,
    campaign: Campaign,
    text: str,
    send: Send,
    *,
    from_link: bool,
    respect_ignore: bool = True,
) -> str | None:
    """Compare a file's text with the campaign's house rules and, if they differ, offer
    the buttons through `send`. Returns words for the DM when there is nothing to offer
    (the files match, or the file can't be used), else None. A version of a linked file the
    DM chose to ignore is skipped unless `respect_ignore` is off (the DM asked to look)."""
    if len(text) > house_file.MAX_FILE_CHARS:  # (parse would refuse it too)
        return TOO_BIG
    parsed = await asyncio.to_thread(house_file.parse, text)
    unreadable = len(parsed.problems)
    if not parsed.rules:
        return no_rules(unreadable)
    fingerprint = house_file_sync.fingerprint(parsed.rules)
    if from_link and respect_ignore:
        links = bot.house_file_links
        link = await links.get(campaign.guild_id, campaign.id) if links is not None else None
        if link is not None and link.ignored == fingerprint:
            return None  # the DM said to ignore this version of the file
    store = bot.house_rules
    if store is None:
        return FAILED
    mine = await store.list(campaign.guild_id, campaign.id)
    diff = house_file.compare(mine, parsed.rules)
    items = house_file_sync.items_of(diff)
    if not items:
        return SAME
    pending = Pending(
        campaign.guild_id,
        campaign.id,
        items,
        fingerprint,
        house_file_sync.summary(diff, unreadable),
        from_link,
    )
    key = bot.house_syncs.add(pending)
    if not await send(offer_text(pending), offer_view(campaign.guild_id, key, pending)):
        bot.house_syncs.drop(key)
    return None


async def read_link(bot: Any, campaign: Campaign) -> tuple[str | None, str | None]:
    """(the linked file's text, or None; words for the DM if it can't be read). The link is
    never in the words."""
    links = bot.house_file_links
    link = await links.get(campaign.guild_id, campaign.id) if links is not None else None
    if link is None:
        return None, None
    try:
        got = await fetch.fetch(link.link)
    except fetch.LinkError as exc:
        return None, couldnt_read(str(exc))
    if not got.filename.endswith(".txt"):
        return None, NOT_TEXT
    return got.data.decode("utf-8", errors="replace"), None


async def check_linked(bot: Any, campaign: Campaign, send: Send, *, quiet_if_same: bool) -> None:
    """Read the campaign's linked file and offer what differs. Says one short line when it
    can't be read; says nothing when there is no link or (at a session start) nothing to
    change. Never raises."""
    try:
        text, problem = await read_link(bot, campaign)
        words = problem
        if text is not None:
            words = await compare_text(
                bot, campaign, text, send, from_link=True, respect_ignore=quiet_if_same
            )
            if words == SAME and quiet_if_same:
                words = None
        if words:
            await send(words, discord.ui.View())
    except Exception:
        log.exception("Couldn't compare the house-rules file")
