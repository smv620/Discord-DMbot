"""🤝 Hand over a campaign (#437 part 1b, decided on #437 2026-10-08): the campaign's
owner offers it to another subscriber, and the person offered answers in a private
message. Ownership moves only when they accept, within 7 days, and only if they still
have a free campaign slot then. The rules are all in `CampaignStore`; this is the Discord
side.

- ⚙️ Settings shows who owns the campaign and, for the owner, **🤝 Hand over** (pick a
  member of the server), or **Withdraw offer** while one waits for an answer.
- The person offered gets a private message with **Accept** / **No thanks**. If DMbot
  can't reach them (they left the server, or their messages are closed), the offer is
  taken back at once and the owner is told (decision 3).
- `/dmbot start` on a campaign with no owner yet (from before owners were recorded, with
  several DMs) asks the DM who started it to take it on (decision 1). Until the plan
  checks are live, "Not now" changes nothing and the session runs anyway.

Every button carries what it needs (server, campaign or offer) so it works after a
restart. Register with `bot.add_dynamic_items(HandoverButton, WithdrawOfferButton,
AcceptOfferButton, DeclineOfferButton, TakeOnButton, NotNowButton)`.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, cast

import discord

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.models import HANDOVER_DAYS, HandoverOffer
from dmbot.campaigns.store import NO_OWNER_YET, NOT_THE_OWNER
from dmbot.logs import log_context

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_CAMPAIGN = r"(?P<campaign>[0-9a-f]{32})"
_GUILD = r"(?P<guild>[0-9]{1,20})"
_OFFER = r"(?P<offer>[0-9]{1,19})"

HANDOVER_LABEL = "Hand over"
WITHDRAW_LABEL = "Withdraw offer"
ACCEPT_LABEL = "Accept"
DECLINE_LABEL = "No thanks"
TAKE_ON_LABEL = "Take it on"
NOT_NOW_LABEL = "Not now"

PICK = (
    "🤝 Who should take over **{campaign}**? It would use their plan's hours and one of "
    "their campaign slots, and they'd become one of its DMs; you stay a DM. They get a "
    "private message to accept or say no, and nothing changes until they accept."
)
PICK_PLACEHOLDER = "Pick who takes it over"
NOT_A_PERSON = "That's a bot. Pick a person."
OFFER_SENT = (
    "Offer sent. **{name}** has {days} days to accept. You can withdraw it from ⚙️ Settings."
)
UNREACHABLE = (
    "DMbot couldn't message **{name}**: they may have left the server, or their messages "
    "are closed. The offer was taken back."
)
OFFER_TEXT = (
    "🤝 **{owner}** wants to hand you the campaign **{campaign}** in **{server}**. It would "
    "use one of your campaign slots and your plan's hours, and you'd become one of its "
    "DMs. Answer by {deadline}."
)
ACCEPTED = (
    "✅ **{campaign}** is yours now. It uses your plan from now on, and you're one of its "
    "DMs in **{server}**."
)
TOLD_ACCEPTED = "✅ **{name}** accepted: **{campaign}** uses their plan now. You're still a DM."
NO_FREE_SLOT = (
    "You have no free campaign slot, or your plan has stopped. Free one, or pick a plan, "
    "then press **Accept** again."
)
DECLINED = "Done. DMbot will tell **{owner}**."
TOLD_DECLINED = "**{name}** said no thanks to **{campaign}**. It stays yours."
ENDED = "This offer has ended: it was answered, taken back, or its {days} days are up."
WITHDRAWN = "Offer taken back. **{campaign}** stays yours."
NOT_HERE = "DMbot can't do this from here just now. Please try again in a minute."
NOT_A_MEMBER = "You're not in that server any more, so you can't take over its campaign."
FAILED = "Something broke on DMbot's side, so that didn't work. Try once more."
TAKE_ON_ASK = (
    "**{campaign}** has no owner yet: the DM whose plan it uses. Take it on with your plan? "
    "Once you do, you can hand it over to someone else later."
)
TAKEN = "✅ **{campaign}** uses your plan now."
TAKE_NO_ROOM = (
    "You need a DMbot plan with a free campaign slot to take it on. You can still play: "
    "DMbot will ask again next time."
)
TAKE_GONE = "Someone already took this campaign on, so nothing changed."
NOT_NOW = "OK. DMbot will ask again next time you start this campaign."
ENDED_CAMPAIGN = "I can't find this campaign anymore. It may have been deleted."


def _now() -> int:
    return int(time.time())


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


async def _broke(interaction: discord.Interaction, what: str) -> None:
    """A step failed after answering: say so privately, never leave it hanging."""
    log.exception("%s failed", what)
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(FAILED, ephemeral=True)
        else:
            await interaction.response.send_message(FAILED, ephemeral=True)


def _store(interaction: discord.Interaction) -> CampaignStore:
    return cast(CampaignStore, interaction.client.campaigns)  # type: ignore[attr-defined]


def owner_line(campaign: Campaign, offer: HandoverOffer | None) -> str:
    """For the ⚙️ Settings card: whose plan the campaign uses, and any offer waiting."""
    if campaign.owner_user_id is None:
        line = "• **Owner:** none yet. Run `/dmbot start` and take it on, so it uses your plan."
    else:
        line = f"• **Owner:** <@{campaign.owner_user_id}>. The campaign uses their plan."
    if offer is not None:
        line += (
            f" Offered to **{_md(offer.to_name)}** until "
            f"{discord.utils.format_dt(_at(offer.expires_at), 'f')}."
        )
    return line


def owner_buttons(campaign: Campaign, offer: HandoverOffer | None) -> list[discord.ui.Item[Any]]:
    """The card's hand-over button: withdraw a waiting offer, or hand over."""
    if offer is not None:
        return [WithdrawOfferButton(campaign.guild_id, offer.id)]
    return [HandoverButton(campaign.id)]


async def _owner_only(interaction: discord.Interaction, campaign: Campaign) -> bool:
    """Only the owner hands over or withdraws. Tells anyone else why."""
    if campaign.owner_user_id is None:
        await interaction.response.send_message(NO_OWNER_YET, ephemeral=True)
        return False
    if campaign.owner_user_id != interaction.user.id:
        await interaction.response.send_message(NOT_THE_OWNER, ephemeral=True)
        return False
    return True


class HandoverButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:handover:{_CAMPAIGN}",
):
    """🤝 Hand over, on ⚙️ Settings: the owner picks who takes the campaign over."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=HANDOVER_LABEL,
                emoji="🤝",
                style=discord.ButtonStyle.secondary,
                row=2,
                custom_id=f"dmbot:handover:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HandoverButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await self._run(interaction)
        except Exception:
            await _broke(interaction, "HandoverButton")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        campaign = (
            None if guild is None else await _store(interaction).get(guild.id, self.campaign_id)
        )
        if campaign is None:
            await interaction.response.send_message(ENDED_CAMPAIGN, ephemeral=True)
            return
        if not await _owner_only(interaction, campaign):
            return
        await interaction.response.send_message(
            PICK.format(campaign=_md(campaign.name)),
            view=PickNewOwner(campaign),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class PickNewOwner(discord.ui.View):
    """Who takes the campaign over: any member of the server. The store checks they
    have a plan; DMbot checks it can reach them when it delivers the offer."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign = campaign
        self.pick: discord.ui.UserSelect[PickNewOwner] = discord.ui.UserSelect(
            placeholder=PICK_PLACEHOLDER, min_values=1, max_values=1
        )
        self.pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(self.pick)

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]
    ) -> None:
        log.error("Handing over a campaign failed", exc_info=error)
        with contextlib.suppress(discord.HTTPException):
            if interaction.response.is_done():
                await interaction.followup.send(FAILED, ephemeral=True)
            else:
                await interaction.response.send_message(FAILED, ephemeral=True)

    async def _picked(self, interaction: discord.Interaction) -> None:
        picked = self.pick.values[0]
        if picked.bot:
            await interaction.response.send_message(NOT_A_PERSON, ephemeral=True)
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)  # database, DMs
        store = _store(interaction)
        try:
            offer = await store.offer_handover(
                guild.id,
                self.campaign.id,
                interaction.user.id,
                picked.id,
                _now(),
                from_name=interaction.user.display_name,
                to_name=picked.display_name,
            )
        except CampaignError as exc:
            await interaction.followup.send(str(exc), ephemeral=True, allowed_mentions=NO_PINGS)
            return
        self.stop()
        name = _md(picked.display_name)
        if await deliver_offer(guild, self.campaign, offer):
            text = OFFER_SENT.format(name=name, days=HANDOVER_DAYS)
        else:
            await store.withdraw_handover(guild.id, offer.id, interaction.user.id, _now())
            text = UNREACHABLE.format(name=name)
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(content=text, view=None)
        if interaction.message is not None:  # the picker, now answered
            with contextlib.suppress(discord.HTTPException):
                await interaction.followup.edit_message(interaction.message.id, view=None)


def offer_view(guild_id: int, offer_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AcceptOfferButton(guild_id, offer_id))
    view.add_item(DeclineOfferButton(guild_id, offer_id))
    return view


async def deliver_offer(guild: discord.Guild, campaign: Campaign, offer: HandoverOffer) -> bool:
    """The private message to the person offered. False if they aren't in the server or
    can't be messaged (the caller then takes the offer back). Never raises."""
    try:
        member = guild.get_member(offer.to_user_id) or await guild.fetch_member(offer.to_user_id)
        deadline = discord.utils.format_dt(_at(offer.expires_at), "f")
        await member.send(
            OFFER_TEXT.format(
                owner=_md(offer.from_name),
                campaign=_md(campaign.name),
                server=_md(guild.name),
                deadline=deadline,
            ),
            view=offer_view(guild.id, offer.id),
            allowed_mentions=NO_PINGS,
        )
    except discord.HTTPException as exc:  # not a member (404), messages closed (403)…
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            log.info("Couldn't deliver a hand-over offer to user %s: %s", offer.to_user_id, exc)
        return False
    return True


async def _tell_person(guild: discord.Guild, user_id: int, text: str) -> None:
    """A private note to the other person (best effort: they may have left or closed
    their messages, and the answer stands either way)."""
    with contextlib.suppress(discord.HTTPException):
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        await member.send(text, allowed_mentions=NO_PINGS)


async def _offer_guild(interaction: discord.Interaction, guild_id: int) -> discord.Guild | None:
    """The server an answer in a private message is about, if this process serves it."""
    guild = interaction.client.get_guild(guild_id)
    if guild is None:
        await interaction.response.send_message(NOT_HERE, ephemeral=True)
    return guild


async def _end_message(interaction: discord.Interaction, text: str) -> None:
    """Replace the offer with what happened (its buttons go)."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=None)


class AcceptOfferButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:offer:yes:{_GUILD}:{_OFFER}",
):
    def __init__(self, guild_id: int, offer_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=ACCEPT_LABEL,
                emoji="✅",
                style=discord.ButtonStyle.success,
                custom_id=f"dmbot:offer:yes:{guild_id}:{offer_id}",
            )
        )
        self.guild_id = guild_id
        self.offer_id = offer_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> AcceptOfferButton:
        return cls(int(match["guild"]), int(match["offer"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await self._run(interaction)
        except Exception:
            await _broke(interaction, "AcceptOfferButton")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = await _offer_guild(interaction, self.guild_id)
        if guild is None:
            return
        await interaction.response.defer()  # the database: answer Discord first
        store = _store(interaction)
        try:
            await guild.fetch_member(interaction.user.id)  # no members intent: ask Discord
        except discord.NotFound:
            await interaction.followup.send(NOT_A_MEMBER, ephemeral=True)
            return
        except discord.HTTPException:
            pass  # Discord couldn't say: the accept itself still checks the offer
        now = _now()
        result = await store.accept_handover(self.guild_id, self.offer_id, interaction.user.id, now)
        offer = await store.get_offer(self.guild_id, self.offer_id, now)
        campaign = None if offer is None else await store.get(self.guild_id, offer.campaign_id)
        if result == "no_free_slot":
            await interaction.followup.send(NO_FREE_SLOT, ephemeral=True)
            return
        if result == "gone" or offer is None or campaign is None:
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        await _end_message(
            interaction, ACCEPTED.format(campaign=_md(campaign.name), server=_md(guild.name))
        )
        await _tell_person(
            guild,
            offer.from_user_id,
            TOLD_ACCEPTED.format(name=_md(offer.to_name), campaign=_md(campaign.name)),
        )


class DeclineOfferButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:offer:no:{_GUILD}:{_OFFER}",
):
    def __init__(self, guild_id: int, offer_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=DECLINE_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:offer:no:{guild_id}:{offer_id}",
            )
        )
        self.guild_id = guild_id
        self.offer_id = offer_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> DeclineOfferButton:
        return cls(int(match["guild"]), int(match["offer"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await self._run(interaction)
        except Exception:
            await _broke(interaction, "DeclineOfferButton")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = await _offer_guild(interaction, self.guild_id)
        if guild is None:
            return
        await interaction.response.defer()
        store = _store(interaction)
        now = _now()
        result = await store.decline_handover(
            self.guild_id, self.offer_id, interaction.user.id, now
        )
        offer = await store.get_offer(self.guild_id, self.offer_id, now)
        if result == "gone" or offer is None:
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        await _end_message(interaction, DECLINED.format(owner=_md(offer.from_name)))
        campaign = await store.get(self.guild_id, offer.campaign_id)
        if campaign is not None:
            await _tell_person(
                guild,
                offer.from_user_id,
                TOLD_DECLINED.format(name=_md(offer.to_name), campaign=_md(campaign.name)),
            )


class WithdrawOfferButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:offer:back:{_GUILD}:{_OFFER}",
):
    """On ⚙️ Settings while an offer waits: the owner takes it back."""

    def __init__(self, guild_id: int, offer_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=WITHDRAW_LABEL,
                style=discord.ButtonStyle.secondary,
                row=2,
                custom_id=f"dmbot:offer:back:{guild_id}:{offer_id}",
            )
        )
        self.guild_id = guild_id
        self.offer_id = offer_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> WithdrawOfferButton:
        return cls(int(match["guild"]), int(match["offer"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await self._run(interaction)
        except Exception:
            await _broke(interaction, "WithdrawOfferButton")

    async def _run(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        store = _store(interaction)
        now = _now()
        result = await store.withdraw_handover(
            self.guild_id, self.offer_id, interaction.user.id, now
        )
        offer = await store.get_offer(self.guild_id, self.offer_id, now)
        if result == "gone" or offer is None:
            await interaction.followup.send(ENDED.format(days=HANDOVER_DAYS), ephemeral=True)
            return
        campaign = await store.get(self.guild_id, offer.campaign_id)
        name = _md(campaign.name) if campaign else "The campaign"
        await interaction.followup.send(WITHDRAWN.format(campaign=name), ephemeral=True)


# ---- /dmbot start on a campaign with no owner yet --------------------------------------


def take_on_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TakeOnButton(campaign_id))
    view.add_item(NotNowButton(campaign_id))
    return view


async def ask_to_take_on(interaction: discord.Interaction, campaign_id: str) -> None:
    """After `/dmbot start`: if the campaign has no owner yet and this person is one of
    its DMs, ask them privately to take it on (never guessed: it spends their hours)."""
    guild = interaction.guild
    if guild is None:
        return
    try:
        campaign = await _store(interaction).get(guild.id, campaign_id)
    except Exception:
        log.exception("Couldn't check whether a campaign has an owner")
        return
    if (
        campaign is None
        or campaign.owner_user_id is not None
        or interaction.user.id not in campaign.dm_user_ids
    ):
        return
    with contextlib.suppress(discord.HTTPException):
        await interaction.followup.send(
            TAKE_ON_ASK.format(campaign=_md(campaign.name)),
            view=take_on_view(campaign.id),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


class TakeOnButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:takeon:{_CAMPAIGN}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=TAKE_ON_LABEL,
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:takeon:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> TakeOnButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await self._run(interaction)
        except Exception:
            await _broke(interaction, "TakeOnButton")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(NOT_HERE, ephemeral=True)
            return
        await interaction.response.defer()
        store = _store(interaction)
        result = await store.take_ownership(guild.id, self.campaign_id, interaction.user.id, _now())
        if result == "no_free_slot":
            await interaction.followup.send(TAKE_NO_ROOM, ephemeral=True)
            return
        if result == "gone":
            await _end_message(interaction, TAKE_GONE)
            return
        campaign = await store.get(guild.id, self.campaign_id)
        name = _md(campaign.name) if campaign else "The campaign"
        await _end_message(interaction, TAKEN.format(campaign=name))


class NotNowButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:takeon:later:{_CAMPAIGN}",
):
    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=NOT_NOW_LABEL,
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:takeon:later:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> NotNowButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.edit_message(content=NOT_NOW, view=None)
