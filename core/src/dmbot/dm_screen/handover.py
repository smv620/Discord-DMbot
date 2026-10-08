"""🤝 Hand over a campaign (#437 part 1b, decided on #437 2026-10-08): the campaign's
owner offers it to another subscriber, and the person offered answers in a private
message. Ownership moves only when they accept, within 7 days, and only if their plan
has room for it then. The rules are all in `CampaignStore`; this is the Discord side.

- ⚙️ Settings shows who owns the campaign and, for the owner, **🤝 Hand over** (pick a
  member of the server), or **Take back offer** while one waits for an answer.
- The person offered gets a private message with **Accept** / **No thanks**. If DMbot
  can't reach them (they left the server, or their messages are closed), the offer is
  taken back at once and the owner is told (decision 3).
- `/dmbot start` on a campaign with no owner yet (from before owners were recorded, with
  several DMs) asks the DM who started it to take it on (decision 1). Until the plan
  checks are live, "Not now" changes nothing and the session runs anyway.

Every button carries what it needs (server, campaign or offer) so it works after a
restart; the picker itself is a short-lived menu. Answers in private messages may come to
a process that doesn't serve that server (sharding): the server is then looked up from
Discord, and everything else comes from the database. Each message is built from what
was read before the change is saved, so a failure after saving never says it failed.
Register with `bot.add_dynamic_items(HandoverButton, WithdrawOfferButton,
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
WITHDRAW_LABEL = "Take back offer"
ACCEPT_LABEL = "Accept"
DECLINE_LABEL = "No thanks"
TAKE_ON_LABEL = "Take it on"
NOT_NOW_LABEL = "Not now"

PICK = (
    "🤝 Who should take over **{campaign}**? Nothing changes until they accept. They get a "
    "private message, and if they say yes, the campaign uses their plan's hours instead of "
    "yours. You stay one of its DMs."
)
PICK_PLACEHOLDER = "Pick who takes it over"
NOT_A_PERSON = "That's a bot. Pick a person."
OFFER_SENT = (
    "Offer sent to **{name}**. Nothing changes until they accept (they have {days} days). "
    "To take it back: ⚙️ Settings, then **Take back offer**."
)
# Keep in step with site_offers.NOT_SENT (the same, for an offer made on the website).
UNREACHABLE = (
    "DMbot couldn't send **{name}** a private message (they may have left the server, or "
    "they don't accept messages from server members). The offer was taken back. Ask them to "
    "allow messages from this server, then try again."
)
UNREACHABLE_STUCK = (
    "DMbot couldn't send **{name}** a private message, and couldn't take the offer back "
    "either. Take it back yourself: ⚙️ Settings, then **Take back offer**."
)
OFFER_TEXT = (
    "🤝 **{owner}** wants to hand you the campaign **{campaign}** (server **{server}**). If "
    "you accept, it takes room for one campaign on your plan, and every session of it, "
    "whoever runs it, uses your plan's hours. You become one of its DMs; {owner} stays one. "
    "Answer by {deadline}."
)
ACCEPTED = (
    "✅ You own **{campaign}** now: it uses your plan from now on, and you're one of its "
    "DMs in **{server}**. You'll see its DM screen when its next session starts."
)
TOLD_ACCEPTED = "✅ **{name}** accepted: **{campaign}** uses their plan now. You're still a DM."
NO_FREE_SLOT = (
    "Your plan has no room for another campaign, or it has stopped. Make room or pick a "
    "plan on the DMbot website, then tap **Accept** again (the offer lasts {days} days from "
    "when it was sent)."
)
DECLINED = "You said no thanks. Nothing changed. DMbot will let **{owner}** know."
TOLD_DECLINED = "**{name}** said no thanks to **{campaign}**. It stays yours."
ENDED = "This offer has ended: it was answered, taken back, or its {days} days are up."
# When an offer's days are up (#690): the person's message loses its buttons, and the
# owner is told.
OFFER_EXPIRED = (
    "**{owner}**'s offer of **{campaign}** has ended: its {days} days are up. Nothing "
    "changed. If you still want it, ask {owner} to offer it again."
)
_OFFER_AGAIN = (
    "It stays yours. To offer it again: ⚙️ Settings, then **Hand over**, or your account "
    "page on the DMbot website."
)
TOLD_EXPIRED = (
    "**{name}** didn't answer your offer of **{campaign}** within {days} days, so it ended. "
    + _OFFER_AGAIN
)
# An offer made on the website that DMbot never managed to send (Discord kept failing).
TOLD_EXPIRED_UNSENT = (
    "DMbot couldn't get your offer of **{campaign}** to **{name}** within {days} days, so "
    "it ended. " + _OFFER_AGAIN
)
WITHDRAWN = "Offer taken back. **{campaign}** stays yours."
TOLD_WITHDRAWN = "**{owner}** took back the offer of **{campaign}**. Nothing changed for you."
NOT_YOUR_OFFER = "Only **{owner}**, who made this offer, can take it back."
NOT_HERE = "DMbot can't reach that server right now. Try again in a minute."
SERVER_GONE = "DMbot isn't in that server any more, so this offer can't be answered."
NOT_A_MEMBER = "You're not in that server any more, so you can't take over its campaign."
FAILED = "Something broke on DMbot's side, so that didn't work. Try once more."
TAKE_ON_ASK = (
    "**{campaign}** needs an owner: the DM whose plan pays for its hours. Use your plan for "
    "it? Every session of it will use your hours. Nothing else changes, and you can hand it "
    "over later."
)
TAKEN = "✅ You own **{campaign}** now, so it uses your plan. To hand it over later: ⚙️ Settings."
TAKE_NO_ROOM = (
    "You need a DMbot plan with room for another campaign to take it on. You can still "
    "play: DMbot will ask again next time."
)
TAKE_GONE = "Someone already took this campaign on, so nothing changed."
NOT_NOW = "OK. DMbot will ask again next time you start this campaign."
ENDED_CAMPAIGN = "DMbot can't find this campaign any more. It may have been deleted."


def _now() -> int:
    return int(time.time())


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _store(interaction: discord.Interaction) -> CampaignStore:
    return cast(CampaignStore, interaction.client.campaigns)  # type: ignore[attr-defined]


async def _say(interaction: discord.Interaction, text: str) -> None:
    """A private reply, before or after answering Discord."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
    else:
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=NO_PINGS)


async def _broke(interaction: discord.Interaction, what: str) -> None:
    """A step failed: say so privately, never leave it hanging."""
    log.exception("%s failed", what)
    with contextlib.suppress(discord.HTTPException):
        await _say(interaction, FAILED)


def owner_line(campaign: Campaign, offer: HandoverOffer | None) -> str:
    """For the ⚙️ Settings card: whose plan the campaign uses, and any offer waiting."""
    if campaign.owner_user_id is None:
        line = "• **Owner:** none yet. Run `/dmbot start` and take it on, so it uses your plan."
    else:
        line = f"• **Owner:** <@{campaign.owner_user_id}>. This campaign uses their plan's hours."
    if offer is not None:
        until = discord.utils.format_dt(_at(offer.expires_at), "f")
        line += f" Offered to **{md(offer.to_name)}**; waiting for an answer until {until}."
    return line


def owner_buttons(campaign: Campaign, offer: HandoverOffer | None) -> list[discord.ui.Item[Any]]:
    """The card's hand-over button: take back a waiting offer, or hand over."""
    if offer is not None:
        return [WithdrawOfferButton(campaign.guild_id, offer.id)]
    return [HandoverButton(campaign.id)]


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
            await _broke(interaction, "Hand over")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await _say(interaction, ENDED_CAMPAIGN)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)  # the database
        campaign = await _store(interaction).get(guild.id, self.campaign_id)
        if campaign is None:
            await _say(interaction, ENDED_CAMPAIGN)
        elif campaign.owner_user_id is None:
            await _say(interaction, NO_OWNER_YET)
        elif campaign.owner_user_id != interaction.user.id:
            await _say(interaction, NOT_THE_OWNER)
        else:
            await interaction.followup.send(
                PICK.format(campaign=md(campaign.name)),
                view=PickNewOwner(campaign),
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )


class PickNewOwner(discord.ui.View):
    """Who takes the campaign over: any member of the server. The store checks they
    have a plan; DMbot checks it can reach them when it delivers the offer. A short-lived
    menu (not kept over a restart): press 🤝 Hand over again."""

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
            await _say(interaction, FAILED)

    async def _picked(self, interaction: discord.Interaction) -> None:
        picked = self.pick.values[0]
        if picked.bot:
            await _say(interaction, NOT_A_PERSON)
            return
        guild = interaction.guild
        if guild is None:
            await _say(interaction, NOT_HERE)
            return
        await interaction.response.defer()  # this menu becomes the answer below
        store = _store(interaction)
        name = picked.display_name or picked.name
        try:
            offer = await store.offer_handover(
                guild.id,
                self.campaign.id,
                interaction.user.id,
                picked.id,
                _now(),
                from_name=interaction.user.display_name or interaction.user.name,
                to_name=name,
            )
        except CampaignError as exc:
            await _say(interaction, str(exc))
            return
        self.stop()
        message = await deliver_offer(guild, self.campaign, offer)
        if message is not None:
            text = OFFER_SENT.format(name=md(name), days=HANDOVER_DAYS)
            try:  # so its buttons can be taken off when the offer's days are up
                await store.set_offer_message(guild.id, offer.id, message.id)
            except Exception:
                log.exception("Couldn't save which message holds a hand-over offer")
        else:
            try:
                await store.withdraw_handover(guild.id, offer.id, interaction.user.id, _now())
                text = UNREACHABLE.format(name=md(name))
            except Exception:
                log.exception("Couldn't take back an offer that couldn't be delivered")
                text = UNREACHABLE_STUCK.format(name=md(name))
        await interaction.edit_original_response(content=text, view=None, allowed_mentions=NO_PINGS)


def offer_view(guild_id: int, offer_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(AcceptOfferButton(guild_id, offer_id))
    view.add_item(DeclineOfferButton(guild_id, offer_id))
    return view


async def deliver_offer(
    guild: discord.Guild,
    campaign: Campaign,
    offer: HandoverOffer,
    *,
    raise_if_discord_fails: bool = False,
) -> discord.Message | None:
    """The private message to the person offered, once sent. None if they aren't in the
    server or can't be messaged (the caller then takes the offer back). Never raises, unless
    `raise_if_discord_fails`: then any other Discord error is raised, so the caller can
    try again later instead (offers made on the website, #690)."""
    try:
        member = guild.get_member(offer.to_user_id) or await guild.fetch_member(offer.to_user_id)
        return await member.send(
            OFFER_TEXT.format(
                owner=md(offer.from_name),
                campaign=md(campaign.name),
                server=md(guild.name),
                deadline=discord.utils.format_dt(_at(offer.expires_at), "f"),
            ),
            view=offer_view(guild.id, offer.id),
            allowed_mentions=NO_PINGS,
        )
    except discord.HTTPException as exc:  # not a member (404), messages closed (403)…
        if raise_if_discord_fails and not isinstance(exc, discord.Forbidden | discord.NotFound):
            raise
        with log_context(guild_id=guild.id, campaign_id=campaign.id):
            log.info("Couldn't deliver a hand-over offer to user %s: %s", offer.to_user_id, exc)
        return None


async def _tell_person(guild: discord.Guild, user_id: int, text: str) -> None:
    """A private note to the other person (best effort: they may have left or closed
    their messages, and the answer stands either way)."""
    with contextlib.suppress(discord.HTTPException):
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        await member.send(text, allowed_mentions=NO_PINGS)


async def _offer_guild(interaction: discord.Interaction, guild_id: int) -> discord.Guild | None:
    """The server an answer in a private message is about (after answering Discord). Not
    cached here when another process serves it: looked up from Discord instead."""
    guild = interaction.client.get_guild(guild_id)
    if guild is not None:
        return guild
    try:
        return await interaction.client.fetch_guild(guild_id)
    except discord.NotFound:
        await _say(interaction, SERVER_GONE)
    except discord.HTTPException:
        await _say(interaction, NOT_HERE)
    return None


async def _end_message(interaction: discord.Interaction, text: str) -> None:
    """Replace the offer with what happened (its buttons go)."""
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=None, allowed_mentions=NO_PINGS)


async def _offer_and_campaign(
    store: CampaignStore, guild_id: int, offer_id: int, now: int
) -> tuple[HandoverOffer | None, Campaign | None]:
    """What the messages need, read before anything is saved."""
    offer = await store.get_offer(guild_id, offer_id, now)
    campaign = None if offer is None else await store.get(guild_id, offer.campaign_id)
    return offer, campaign


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
            await _broke(interaction, "Accepting a hand-over")

    async def _run(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # Discord and the database: answer first
        guild = await _offer_guild(interaction, self.guild_id)
        if guild is None:
            return
        me = interaction.user.id
        store = _store(interaction)
        now = _now()
        offer, campaign = await _offer_and_campaign(store, self.guild_id, self.offer_id, now)
        if offer is None or campaign is None or offer.to_user_id != me:
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        accepted = ACCEPTED.format(campaign=md(campaign.name), server=md(guild.name))
        if offer.status == "accepted":  # pressed again: say it again
            await _end_message(interaction, accepted)
            return
        try:
            await guild.fetch_member(me)  # no members intent: ask Discord
        except discord.NotFound:
            await _say(interaction, NOT_A_MEMBER)
            return
        except discord.HTTPException:
            pass  # Discord couldn't say: the store still checks the offer
        result = await store.accept_handover(self.guild_id, self.offer_id, me, now)
        if result == "no_free_slot":
            await _say(interaction, NO_FREE_SLOT.format(days=HANDOVER_DAYS))
        elif result == "gone":
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
        else:
            await _end_message(interaction, accepted)
            await _tell_person(
                guild,
                offer.from_user_id,
                TOLD_ACCEPTED.format(name=md(offer.to_name), campaign=md(campaign.name)),
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
            await _broke(interaction, "Declining a hand-over")

    async def _run(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild = await _offer_guild(interaction, self.guild_id)
        if guild is None:
            return
        me = interaction.user.id
        store = _store(interaction)
        now = _now()
        offer, campaign = await _offer_and_campaign(store, self.guild_id, self.offer_id, now)
        if offer is None or offer.to_user_id != me:
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        declined = DECLINED.format(owner=md(offer.from_name))
        if offer.status == "declined":  # pressed again: say it again
            await _end_message(interaction, declined)
            return
        if await store.decline_handover(self.guild_id, self.offer_id, me, now) == "gone":
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        await _end_message(interaction, declined)
        if campaign is not None:
            await _tell_person(
                guild,
                offer.from_user_id,
                TOLD_DECLINED.format(name=md(offer.to_name), campaign=md(campaign.name)),
            )


class WithdrawOfferButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:offer:back:{_GUILD}:{_OFFER}",
):
    """On ⚙️ Settings while an offer waits: the owner takes it back, and the card shows
    the campaign as it is now."""

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
            await _broke(interaction, "Taking back a hand-over offer")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:  # only in its own server
            await _say(interaction, NOT_HERE)
            return
        await interaction.response.defer()  # the card is redrawn below
        store = _store(interaction)
        now = _now()
        offer, campaign = await _offer_and_campaign(store, self.guild_id, self.offer_id, now)
        if offer is None or campaign is None or not offer.is_open(now):
            await _say(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        if offer.from_user_id != interaction.user.id:
            await _say(interaction, NOT_YOUR_OFFER.format(owner=md(offer.from_name)))
            return
        if (
            await store.withdraw_handover(self.guild_id, self.offer_id, offer.from_user_id, now)
            == "gone"
        ):
            await _say(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        from dmbot.dm_screen.settings import settings_text, settings_view  # it imports this

        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=settings_text(campaign), view=settings_view(campaign),
                allowed_mentions=NO_PINGS,
            )  # fmt: skip
        await _say(interaction, WITHDRAWN.format(campaign=md(campaign.name)))
        await _tell_person(
            guild,
            offer.to_user_id,
            TOLD_WITHDRAWN.format(owner=md(offer.from_name), campaign=md(campaign.name)),
        )


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
            TAKE_ON_ASK.format(campaign=md(campaign.name)),
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
            await _broke(interaction, "Taking on a campaign")

    async def _run(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await _say(interaction, NOT_HERE)
            return
        await interaction.response.defer()
        store = _store(interaction)
        campaign = await store.get(guild.id, self.campaign_id)  # its name, before saving
        if campaign is None:
            await _end_message(interaction, ENDED_CAMPAIGN)
            return
        result = await store.take_ownership(guild.id, self.campaign_id, interaction.user.id, _now())
        if result == "no_free_slot":
            await _say(interaction, TAKE_NO_ROOM)
        elif result == "gone":
            await _end_message(interaction, TAKE_GONE)
        else:
            await _end_message(interaction, TAKEN.format(campaign=md(campaign.name)))


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
        try:
            await interaction.response.edit_message(
                content=NOT_NOW, view=None, allowed_mentions=NO_PINGS
            )
        except Exception:
            await _broke(interaction, "Not now")
