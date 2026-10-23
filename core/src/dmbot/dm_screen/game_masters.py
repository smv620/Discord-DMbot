"""👑 Game masters, in ⚙️ Settings → 🗂️ Campaign management (#1600, item 2 of
#618): a campaign's DMs add and remove each other. Everything in DMbot already checks
`dm_user_ids`; this is the only way to change them.

- **Who:** only a DM of the campaign, checked on every press (a server manager who is not a DM
  does not get the button).
- **Add:** pick a member of the server. Never a bot, never someone who already is one, at most
  `MAX_DMS` (the store enforces it). The new game master gets one private message. Adding never
  touches their consent to be recorded.
- **Remove:** pick one, then one confirmation. Never whose plan the campaign uses (hand it over
  first) and never the last game master. A game master may remove themselves.
- **Access follows at once:** after either change the DM screen's permissions (and an existing
  game-time channel's) are applied again, and a running session's DM set is updated
  (`DMBot.after_screen_change`), so a removed game master loses the screen mid-session and a new
  one gets it.

Every change is logged with who did it (ids in the log only, never in a channel). The menus are
short-lived and rebuilt from the database on every press; the button's ID carries the campaign
so it works after a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.store import LAST_DM, MAX_DMS, OWNER_STAYS, TOO_MANY_DMS
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import DMScreenError, setup_dm_screen
from dmbot.dm_screen.gametime_channel import setup_gametime_channel
from dmbot.logs import log_context
from dmbot.mentions import NO_PINGS

log = logging.getLogger(__name__)

_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Game masters"
ONLY_DMS = "Only this campaign's game masters can change this list."
GONE = messages.DM_CAMPAIGN_GONE
FAILED = (
    "Sorry, that didn't work. Check the list in ⚙️ Settings → 🗂️ Campaign management "
    "→ 👑 Game masters, then try again."
)
NOT_HERE = "DMbot can't reach the server right now. Try again in a minute."
NOT_A_PERSON = "Bots can't be game masters. Pick a person."
ALREADY = "**{name}** is already a game master."
TOO_MANY = TOO_MANY_DMS
# Two voices, because only the plan holder has the 🤝 Hand over button.
PLAN_HOLDER_SELF = (
    "Your plan runs this campaign, so you can't leave yet. Hand the campaign over first: "
    "⚙️ Settings → 🗂️ Campaign management → 🤝 Hand over."
)
PLAN_HOLDER_OTHER = (
    "**{name}** holds this campaign's plan, so they can't be removed. They can hand the "
    "campaign over first (⚙️ Settings → 🗂️ Campaign management → 🤝 Hand over)."
)
LAST_ONE = LAST_DM
GONE_FROM_LIST = (
    "They aren't a game master any more. Open ⚙️ Settings → 🗂️ Campaign "
    "management → 👑 Game masters to see the list."
)
ADD_PROMPT = "➕ Who should be a game master of **{campaign}**? Pick a member of this server."
ADD_PLACEHOLDER = "Pick who to add"
REMOVE_PLACEHOLDER = "Pick who to remove"
CONFIRM = "**{name}** will no longer see the DM screen or change this campaign. Remove?"
CONFIRM_SELF = "You will no longer see the DM screen or change this campaign. Remove yourself?"
ADDED = (
    "✅ **{name}** is now a game master of **{campaign}**. They can see its DM screen and "
    "change its settings."
)
REMOVED = "✅ **{name}** is no longer a game master of **{campaign}**."
REMOVED_SELF = "✅ You are no longer a game master of **{campaign}**."
NOT_TOLD = "\nDMbot couldn't message them, so please tell them yourself."
ACCESS_LATE = (
    "\nSaved, but DMbot couldn't update who can see the DM screen yet. It will on the next "
    "session, or when you change who can see the DM screen in ⚙️ Settings → 👁️ Who can see "
    "the DM screen."
)
TOLD = (
    "👑 **{by}** made you a game master of **{campaign}** in **{server}**. You can now see its "
    "DM screen and change its settings. This doesn't start any recording of you."
)
MENU_TIMED_OUT = (
    "This menu timed out. Press ⚙️ Settings → 🗂️ Campaign management → 👑 Game masters to try again."
)


def md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _store(interaction: discord.Interaction) -> CampaignStore:
    store: CampaignStore = getattr(interaction.client, "campaigns")  # noqa: B009  # the bot's
    return store


LOOKUP_S = 1.5  # how long the card waits for Discord to name its game masters
LEFT = "someone who left the server"
UNREACHABLE = "a game master"  # Discord could not be asked: no claim either way (#2556)


async def _one_name(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id)  # the bot's cache: DMbot has no members intent (#2556)
    if member is not None:
        return member.display_name
    try:
        return (await guild.fetch_member(user_id)).display_name
    except discord.NotFound:
        return LEFT  # Discord says they are not in the server: the only time "left" is true
    except discord.HTTPException:
        return UNREACHABLE


async def names_of(guild: discord.Guild | None, user_ids: Iterable[int]) -> dict[int, str]:
    """Display names for the card, the cache first and then Discord, all at once and within
    `LOOKUP_S` in all (the button must answer inside Discord's three seconds). A game master is
    called "someone who left the server" only when Discord answers that they did; a name that
    does not come back in time, or fails for any other reason, is "a game master"."""
    ids = sorted(set(user_ids))
    if guild is None:
        return {uid: UNREACHABLE for uid in ids}
    found = await asyncio.gather(
        *(asyncio.wait_for(_one_name(guild, uid), LOOKUP_S) for uid in ids),
        return_exceptions=True,
    )
    return {
        uid: name if isinstance(name, str) else UNREACHABLE
        for uid, name in zip(ids, found, strict=True)
    }


def card_text(campaign: Campaign, names: Mapping[int, str]) -> str:
    """The game masters, one to a line, the one whose plan the campaign uses marked."""
    lines = []
    for user_id in sorted(campaign.dm_user_ids):
        plan = " (plan)" if user_id == campaign.owner_user_id else ""
        lines.append(f"• **{md(names.get(user_id, UNREACHABLE))}**{plan}")
    return (
        f"👑 **Game masters (DMs) of {md(campaign.name)}**\n"
        + "\n".join(lines)
        + "\nThey see the DM screen and change settings. (plan) = whose plan this campaign "
        f"uses. Up to {MAX_DMS}."
    )


def plan_holder_words(target: int, presser: int, names: Mapping[int, str]) -> str:
    """Why the plan holder cannot be removed, in the voice that fits who pressed: only the
    plan holder has the 🤝 Hand over button."""
    if target == presser:
        return PLAN_HOLDER_SELF
    return PLAN_HOLDER_OTHER.format(name=md(names.get(target, UNREACHABLE)))


async def _say(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


async def _a_dm(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Campaign, discord.Guild] | None:
    """The campaign and server, if the person pressing is one of the campaign's DMs right now
    (read again from the database every time); otherwise they are told why."""
    guild = interaction.guild
    if guild is None:
        await _say(interaction, GONE)
        return None
    try:
        campaign = await _store(interaction).get(guild.id, campaign_id)
    except Exception:
        log.exception("Couldn't load a campaign for its game masters")
        await _say(interaction, FAILED)
        return None
    if campaign is None:
        await _say(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _say(interaction, ONLY_DMS)
        return None
    return campaign, guild


class GameMastersButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:gms:{_ID}",
):
    """👑 Game masters, in 🗂️ Campaign management: opens the list privately (DMs only)."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABEL,
                emoji="👑",
                style=discord.ButtonStyle.secondary,
                row=row,
                custom_id=f"dmbot:gms:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> GameMastersButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await open_list(interaction, self.campaign_id)
        except Exception:
            log.exception("Opening the game masters failed")
            with contextlib.suppress(discord.HTTPException):
                await _say(interaction, FAILED)


async def open_list(interaction: discord.Interaction, campaign_id: str) -> None:
    found = await _a_dm(interaction, campaign_id)
    if found is None:
        return
    campaign, guild = found
    names = await names_of(guild, campaign.dm_user_ids)
    await interaction.response.send_message(
        card_text(campaign, names),
        view=ListView(campaign),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


class ListView(discord.ui.View):
    """The list's two buttons. Short-lived: pressing 👑 Game masters again opens a fresh one."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign.id
        add = discord.ui.Button[ListView](
            label="Add a game master", emoji="➕", style=discord.ButtonStyle.primary
        )
        add.callback = self._add  # type: ignore[method-assign]
        remove = discord.ui.Button[ListView](
            label="Remove a game master", emoji="➖", style=discord.ButtonStyle.secondary
        )
        remove.callback = self._remove  # type: ignore[method-assign]
        self.add_item(add)
        self.add_item(remove)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Game masters menu failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            await _say(interaction, FAILED)

    async def _add(self, interaction: discord.Interaction) -> None:
        found = await _a_dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, _guild = found
        await interaction.response.send_message(
            ADD_PROMPT.format(campaign=md(campaign.name)),
            view=AddPicker(campaign.id),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

    async def _remove(self, interaction: discord.Interaction) -> None:
        found = await _a_dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, guild = found
        names = await names_of(guild, campaign.dm_user_ids)
        await interaction.response.send_message(
            "➖ Who should stop being a game master?",
            view=RemovePicker(campaign, names),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class AddPicker(discord.ui.View):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign_id
        self.pick: discord.ui.UserSelect[AddPicker] = discord.ui.UserSelect(
            placeholder=ADD_PLACEHOLDER, min_values=1, max_values=1
        )
        self.pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(self.pick)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Adding a game master failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            await _say(interaction, FAILED)

    async def _picked(self, interaction: discord.Interaction) -> None:
        found = await _a_dm(interaction, self.campaign_id)  # checked again at this press
        if found is None:
            return
        campaign, guild = found
        person = self.pick.values[0]
        name = person.display_name or person.name
        if person.bot:
            await _say(interaction, NOT_A_PERSON)
            return
        if person.id in campaign.dm_user_ids:
            await _say(interaction, ALREADY.format(name=md(name)))
            return
        if len(campaign.dm_user_ids) >= MAX_DMS:
            await _say(interaction, TOO_MANY)
            return
        await interaction.response.defer(ephemeral=True)
        store = _store(interaction)
        try:
            changed = await store.add_dm(guild.id, campaign.id, person.id)
        except CampaignError as exc:
            await _say(interaction, str(exc))
            return
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            log.info("Game master %s added by %s", person.id, interaction.user.id)
        late = await follow_access(interaction, guild, changed)
        told = await _tell_new(guild, changed, person.id, interaction.user.display_name)
        self.stop()
        await interaction.edit_original_response(
            content=ADDED.format(name=md(name), campaign=md(campaign.name))
            + ("" if told else NOT_TOLD)
            + late,
            view=None,
            allowed_mentions=NO_PINGS,
        )


async def _tell_new(guild: discord.Guild, campaign: Campaign, user_id: int, by: str) -> bool:
    """The one private message to a new game master. False if it could not be sent."""
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        await member.send(
            TOLD.format(by=md(by), campaign=md(campaign.name), server=md(guild.name)),
            allowed_mentions=NO_PINGS,
        )
    except discord.HTTPException:
        return False
    return True


class RemovePicker(discord.ui.View):
    def __init__(self, campaign: Campaign, names: Mapping[int, str]) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign.id
        self.names = dict(names)  # the ones the list was built with, reused when one is picked
        options = [
            discord.SelectOption(
                label=(
                    names.get(uid, UNREACHABLE)
                    + (" (plan)" if uid == campaign.owner_user_id else "")
                )[:100],
                value=str(uid),
            )
            for uid in sorted(campaign.dm_user_ids)
        ]
        self.pick: discord.ui.Select[RemovePicker] = discord.ui.Select(
            placeholder=REMOVE_PLACEHOLDER, options=options
        )
        self.pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(self.pick)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Removing a game master failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            await _say(interaction, FAILED)

    async def _picked(self, interaction: discord.Interaction) -> None:
        found = await _a_dm(interaction, self.campaign_id)
        if found is None:
            return
        campaign, _guild = found
        user_id = int(self.pick.values[0])
        if user_id not in campaign.dm_user_ids:
            await _say(interaction, GONE_FROM_LIST)
            return
        if user_id == campaign.owner_user_id:
            await _say(interaction, plan_holder_words(user_id, interaction.user.id, self.names))
            return
        if len(campaign.dm_user_ids) <= 1:
            await _say(interaction, LAST_ONE)
            return
        name = self.names.get(user_id, UNREACHABLE)
        text = CONFIRM_SELF if user_id == interaction.user.id else CONFIRM.format(name=md(name))
        self.stop()
        await interaction.response.edit_message(
            content=text, view=ConfirmRemove(campaign.id, user_id, name)
        )


class ConfirmRemove(discord.ui.View):
    def __init__(self, campaign_id: str, user_id: int, name: str) -> None:
        super().__init__(timeout=5 * 60)
        self.campaign_id, self.user_id, self.name = campaign_id, user_id, name
        yes = discord.ui.Button[ConfirmRemove](label="Remove", style=discord.ButtonStyle.danger)
        yes.callback = self._yes  # type: ignore[method-assign]
        no = discord.ui.Button[ConfirmRemove](label="Cancel", style=discord.ButtonStyle.secondary)
        no.callback = self._no  # type: ignore[method-assign]
        self.add_item(no)  # first: the safe one
        self.add_item(yes)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Removing a game master failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            await _say(interaction, FAILED)

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Nothing was changed.", view=None)

    async def _yes(self, interaction: discord.Interaction) -> None:
        found = await _a_dm(interaction, self.campaign_id)  # the DM, right now
        if found is None:
            return
        campaign, guild = found
        await interaction.response.defer(ephemeral=True)
        try:
            changed = await _store(interaction).remove_dm(guild.id, campaign.id, self.user_id)
        except CampaignError as exc:
            text = (
                plan_holder_words(self.user_id, interaction.user.id, {self.user_id: self.name})
                if str(exc) == OWNER_STAYS
                else str(exc)  # (the last game master, or a campaign that is gone)
            )
            await _say(interaction, text)
            return
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            log.info("Game master %s removed by %s", self.user_id, interaction.user.id)
        late = await follow_access(interaction, guild, changed)
        self.stop()
        mine = self.user_id == interaction.user.id
        done = (
            REMOVED_SELF.format(campaign=md(campaign.name))
            if mine
            else REMOVED.format(name=md(self.name), campaign=md(campaign.name))
        )
        await interaction.edit_original_response(
            content=done + late, view=None, allowed_mentions=NO_PINGS
        )


async def follow_access(
    interaction: discord.Interaction, guild: discord.Guild, campaign: Campaign
) -> str:
    """After the game masters changed: a running session takes the new set at once, then the DM
    screen's permissions (and an existing game-time channel's) are applied again (this creates the
    screen if the campaign has none yet, as a session start would).
    "" when it worked, else the words to add to the reply; the change itself is saved either
    way."""
    store = _store(interaction)
    # A running session takes the new set first and on its own: its authority must not wait for
    # (or fail with) the Discord permissions below.
    running = getattr(interaction.client, "after_game_masters_change", None)
    if running is not None:
        try:
            running(campaign)
        except Exception:
            log.exception("A running session could not take the new game masters")
    try:
        result = await setup_dm_screen(guild, campaign.id, store)
    except DMScreenError as exc:
        log.info("Game masters changed but the DM screen could not be updated: %s", exc)
        return ACCESS_LATE
    except Exception:
        log.exception("Game masters changed but the DM screen could not be updated")
        return ACCESS_LATE
    if campaign.gametime_channel_id is not None:
        try:
            await setup_gametime_channel(guild, campaign.id, store)
        except Exception:
            log.exception("The game-time channel could not be updated after a game master change")
    hook = getattr(interaction.client, "after_screen_change", None)
    if hook is not None:
        try:
            await hook(result.campaign, result.channel)  # a running table's DM set
        except Exception:
            log.exception("A running session could not take the new game masters")
            return ACCESS_LATE
    return ""
