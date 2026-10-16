"""👑 Game masters, on ⚙️ Settings (#1600, item 2 of #618): a campaign's DMs add and remove
each other. Everything in DMbot already checks `dm_user_ids`; this is the only way to change
them.

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

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.store import MAX_DMS, OWNER_STAYS, TOO_MANY_DMS
from dmbot.dm_screen import messages
from dmbot.dm_screen.channel import DMScreenError, setup_dm_screen
from dmbot.dm_screen.gametime_channel import setup_gametime_channel
from dmbot.logs import log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
LABEL = "Game masters"
ONLY_DMS = "Only this campaign's game masters can change who they are."
GONE = messages.DM_CAMPAIGN_GONE
FAILED = "Sorry, that didn't work. Nothing was changed. Please try again."
NOT_HERE = "DMbot can't reach the server right now. Try again in a minute."
NOT_A_PERSON = "That's a bot. Pick a person."
ALREADY = "**{name}** is already a game master."
TOO_MANY = TOO_MANY_DMS
PLAN_HOLDER = "That's whose plan this campaign uses. Hand it over first (⚙️ Settings → Hand over)."
LAST_ONE = "A campaign needs at least one game master. Add another first."
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
NOT_TOLD = " DMbot couldn't send them a private message, so tell them yourself."
ACCESS_LATE = (
    "\nDMbot couldn't update the DM screen's access just now. It does on the next session, or "
    "when you change who can see the DM screen in ⚙️ Settings."
)
TOLD = (
    "👑 **{by}** made you a game master of **{campaign}** in **{server}**. You can now see its "
    "DM screen and change its settings."
)
MENU_TIMED_OUT = "This menu timed out. Press ⚙️ Settings, then 👑 Game masters, to try again."


def md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _store(interaction: discord.Interaction) -> CampaignStore:
    store: CampaignStore = getattr(interaction.client, "campaigns")  # noqa: B009  # the bot's
    return store


def name_of(guild: discord.Guild | None, user_id: int) -> str:
    member = guild.get_member(user_id) if guild is not None else None
    return member.display_name if member is not None else "someone who left the server"


def card_text(campaign: Campaign, guild: discord.Guild | None) -> str:
    """The game masters, one to a line, the one whose plan the campaign uses marked."""
    lines = []
    for user_id in sorted(campaign.dm_user_ids):
        plan = " (plan)" if user_id == campaign.owner_user_id else ""
        lines.append(f"• **{md(name_of(guild, user_id))}**{plan}")
    return (
        f"👑 **Game masters of {md(campaign.name)}**\n"
        "They can see the DM screen and change this campaign's settings. The one marked "
        f"(plan) is whose plan the campaign uses. Up to {MAX_DMS}.\n" + "\n".join(lines)
    )


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
    """👑 Game masters, on ⚙️ Settings: opens the list privately (DMs only)."""

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
    await interaction.response.send_message(
        card_text(campaign, guild),
        view=ListView(campaign),
        ephemeral=True,
        allowed_mentions=NO_PINGS,
    )


class ListView(discord.ui.View):
    """The list's two buttons. Short-lived: pressing ⚙️ Settings again opens a fresh one."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign.id
        add = discord.ui.Button[ListView](
            label="Add a game master", emoji="➕", style=discord.ButtonStyle.primary
        )
        add.callback = self._add  # type: ignore[method-assign]
        remove = discord.ui.Button[ListView](
            label="Remove", emoji="➖", style=discord.ButtonStyle.secondary
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
        await interaction.response.send_message(
            "➖ Who should stop being a game master?",
            view=RemovePicker(campaign, guild),
            ephemeral=True,
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
    def __init__(self, campaign: Campaign, guild: discord.Guild) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign_id = campaign.id
        options = [
            discord.SelectOption(
                label=(name_of(guild, uid) + (" (plan)" if uid == campaign.owner_user_id else ""))[
                    :100
                ],
                value=str(uid),
            )
            for uid in sorted(campaign.dm_user_ids)
        ]
        self.pick: discord.ui.Select[RemovePicker] = discord.ui.Select(
            placeholder=REMOVE_PLACEHOLDER, options=options[:25]
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
        campaign, guild = found
        user_id = int(self.pick.values[0])
        if user_id not in campaign.dm_user_ids:
            await _say(interaction, "They aren't a game master any more.")
            return
        if user_id == campaign.owner_user_id:
            await _say(interaction, PLAN_HOLDER)
            return
        if len(campaign.dm_user_ids) <= 1:
            await _say(interaction, LAST_ONE)
            return
        name = name_of(guild, user_id)
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
            text = PLAN_HOLDER if str(exc) == OWNER_STAYS else str(exc)
            if "at least one DM" in text:
                text = LAST_ONE
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
    """After the game masters changed: apply the DM screen's permissions (and an existing
    game-time channel's) again and let a running session catch up, so the change counts at once.
    "" when it worked, else the words to add to the reply; the change itself is saved either
    way."""
    store = _store(interaction)
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
