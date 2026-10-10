"""🤝 Hand over a campaign (#437 part 1b, decided on #437 2026-10-08): the campaign's
owner offers it to another member of the server, and the person offered answers in a private
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
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

import discord

from dmbot.campaigns import Campaign, CampaignError, CampaignStore
from dmbot.campaigns.models import HANDOVER_DAYS, HandoverOffer
from dmbot.campaigns.store import NO_OWNER_YET, NOT_THE_OWNER
from dmbot.dm_screen.messages import DM_CAMPAIGN_GONE, LOAD_FAILED
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
    "DMbot will send you a private message with their answer. To take it back: "
    "⚙️ Settings, then **Take back offer**."
)
MENU_TIMED_OUT = (
    "This menu timed out, so no offer was sent. To try again: ⚙️ Settings, then 🤝 Hand over."
)
# Keep in step with site_offers.NOT_SENT (the same, for an offer made on the website).
UNREACHABLE = (
    "DMbot couldn't send **{name}** a private message, so the offer was taken back. Check "
    "they're still in this server, and ask them to allow direct messages from this server's "
    "members (their privacy settings for this server). Then press 🤝 Hand over again."
)
UNREACHABLE_STUCK = (
    "DMbot couldn't send **{name}** a private message, and couldn't take the offer back "
    "either. Take it back yourself: ⚙️ Settings, then **Take back offer**."
)
UNREACHABLE_ANSWERED = (
    "DMbot couldn't send **{name}** a private message, but the offer was already answered "
    "(on the DMbot website) or taken back meanwhile. ⚙️ Settings shows where it stands."
)
OFFER_TEXT = (
    "🤝 **{owner}** is asking you to pay for the campaign **{campaign}** on **{server}** with "
    "your DMbot plan.\n\n"
    "Nothing changes unless you tap **Accept**. If you do: every session of it uses your "
    "plan's hours (whoever runs it), it counts as one of your campaigns, and you're added as "
    "one of its DMs, so you can see behind the DM screen (spoilers, if you play in it). "
    "**{owner}** is still one of its DMs.\n\n"
    "If you don't answer by {deadline}, the offer just ends."
)
ACCEPTED = (
    "✅ Done: **{campaign}** uses your plan now, and you're one of its DMs on **{server}**. "
    "**{owner}** is still one of its DMs. DMbot will let **{owner}** know."
)
TOLD_ACCEPTED = (
    "✅ **{name}** accepted: **{campaign}** uses their plan now. You're still one of its DMs; "
    "nothing else changes."
)
NO_FREE_SLOT = (
    "Your DMbot plan isn't working right now (it may have ended), or it has no room for "
    "another campaign. Fix that on the DMbot website, then tap **Accept** again before "
    "{deadline}."
)
DECLINED = "You said no thanks. Nothing changed. DMbot will let **{owner}** know."
TOLD_DECLINED = "**{name}** said no thanks to **{campaign}**. It stays yours."
ENDED = (
    "This offer has ended: it was answered, taken back, or its {days} days are up. If you "
    "still want it, ask the campaign's owner to offer it again."
)
# When an offer's days are up (#690): the person's message loses its buttons, and the
# owner is told.
OFFER_EXPIRED = (
    "**{owner}**'s offer of **{campaign}** has ended: its {days} days are up. Nothing "
    "changed. If you still want it, ask **{owner}** to offer it again."
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
NOT_HERE = "DMbot can't reach the server right now. Try again in a minute."
SERVER_GONE = (
    "DMbot isn't in that server any more, so this offer can't be answered. Nothing changed."
)
NOT_A_MEMBER = (
    "You're not in that server any more, so you can't take over its campaign. Rejoin it, "
    "then tap **Accept** again before the deadline."
)
# An accept is noted in the campaign's #dm-screen too (the owner's private messages may
# be off): it changes whose plan the table uses. Players may read #dm-screen (peek or
# open), so it says only that. A "no thanks" changes nothing for the table: it stays a
# private note to the owner.
SCREEN_ACCEPTED = (
    "🤝 **{name}** accepted: **{campaign}** now uses their DMbot plan. Nothing else changed: "
    "same DMs, same notes."
)
TAKE_ON_ASK = (
    "Use your plan for **{campaign}**? It has no owner yet (the DM whose plan pays for its "
    "hours). Any of its DMs can take it on. If you do, every session of it uses your hours; "
    "nothing else changes, and you can hand it over later. **Not now** changes nothing and "
    "the session runs anyway."
)
TAKEN = "✅ You own **{campaign}** now, so it uses your plan. To hand it over later: ⚙️ Settings."
TAKE_NO_ROOM = (
    "You can still play today: DMbot will ask again next time. To take this campaign on you "
    "need a DMbot plan with room for one more campaign, and you don't have one right now. "
    "Pick a plan on the DMbot website (Try It is free) and tap **Take it on** again, or let "
    "one of the campaign's other DMs take it on (DMbot asks them when they start a session)."
)
TAKE_GONE = "Someone already took this campaign on, so nothing changed."
NOT_NOW = "OK. DMbot will ask again next time you start this campaign."
ENDED_CAMPAIGN = DM_CAMPAIGN_GONE  # one voice with ⚙️ Settings
FAILED = LOAD_FAILED
# How ⚙️ Settings draws its card, for redrawing it after an offer is taken back. Set by
# settings.py (which imports this module, so it can't be imported from here).
Card = Callable[[Campaign, int], tuple[str, discord.ui.View]]  # campaign, who's looking
_settings_card: Card | None = None


def use_settings_card(card: Card) -> None:
    global _settings_card
    _settings_card = card


def _now() -> int:
    return int(time.time())


def _at(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _store(interaction: discord.Interaction) -> CampaignStore:
    store: CampaignStore = getattr(interaction.client, "campaigns")  # noqa: B009  # the bot's
    return store


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
        line = (
            "• **Owner:** none yet. Any of its DMs can press **Take it on** so it uses their "
            "plan's hours (DMbot also asks whoever runs `/dmbot start` next)."
        )
    else:
        line = f"• **Owner:** <@{campaign.owner_user_id}>. This campaign uses their plan's hours."
    if offer is not None:
        until = discord.utils.format_dt(_at(offer.expires_at), "f")
        line += f" Offered to **{md(offer.to_name)}**; waiting for an answer until {until}."
    return line


def owner_buttons(
    campaign: Campaign, offer: HandoverOffer | None, viewer: int
) -> list[discord.ui.Item[Any]]:
    """The card's hand-over button, for the person looking (`viewer`; the card is private):
    Take back offer for whoever made a waiting offer, Hand over for the owner, and Take
    it on for the DMs of a campaign with no owner yet. Nobody else gets one."""
    if offer is not None:
        return (
            [WithdrawOfferButton(campaign.guild_id, offer.id)]
            if viewer == offer.from_user_id
            else []
        )
    if campaign.owner_user_id is None:
        return [TakeOnButton(campaign.id, row=2)] if viewer in campaign.dm_user_ids else []
    return [HandoverButton(campaign.id)] if viewer == campaign.owner_user_id else []


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
            picker = PickNewOwner(campaign)
            picker.message = await interaction.followup.send(
                PICK.format(campaign=md(campaign.name)),
                view=picker,
                ephemeral=True,
                allowed_mentions=NO_PINGS,
                wait=True,
            )


class PickNewOwner(discord.ui.View):
    """Who takes the campaign over: any member of the server, whatever their plan (the
    owner never learns whether someone pays; Accept checks it). DMbot checks it can reach
    them when it delivers the offer. A short-lived menu (not kept over a restart): press
    🤝 Hand over again."""

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=10 * 60)
        self.campaign = campaign
        self.message: discord.WebhookMessage | None = None  # to clear it when it times out
        self.pick: discord.ui.UserSelect[PickNewOwner] = discord.ui.UserSelect(
            placeholder=PICK_PLACEHOLDER, min_values=1, max_values=1
        )
        self.pick.callback = self._picked  # type: ignore[method-assign]
        self.add_item(self.pick)

    async def on_timeout(self) -> None:
        """A menu past its time no longer answers: never leave it looking live."""
        if self.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.message.edit(
                    content=MENU_TIMED_OUT, view=None, allowed_mentions=NO_PINGS
                )

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
            back: Literal["withdrawn", "gone", "failed"]
            try:
                back = await store.withdraw_handover(
                    guild.id, offer.id, interaction.user.id, _now()
                )
            except Exception:
                log.exception("Couldn't take back an offer that couldn't be delivered")
                back = "failed"
            # "gone": answered (on the website) or taken back in the meantime; not ours to
            # call taken back.
            template = {
                "withdrawn": UNREACHABLE,
                "gone": UNREACHABLE_ANSWERED,
                "failed": UNREACHABLE_STUCK,
            }[back]
            text = template.format(name=md(name))
        await _end_message(interaction, text)


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


async def _tell_screen(interaction: discord.Interaction, campaign: Campaign, text: str) -> None:
    """The outcome in the campaign's #dm-screen as well (best effort: the answer stands
    either way). Only from a process that serves the server: another one has no copy of
    the channel, and the private message to the owner still says it."""
    post = getattr(interaction.client, "post", None)
    if campaign.dm_screen_channel_id is None or post is None:
        return
    if interaction.client.get_guild(campaign.guild_id) is None:  # served elsewhere
        return
    try:
        await post(campaign.dm_screen_channel_id, text)  # logs a Discord failure itself
    except Exception:
        with log_context(guild_id=campaign.guild_id, campaign_id=campaign.id):
            log.warning("Couldn't note a hand-over answer on the DM screen", exc_info=True)


async def _offer_guild(interaction: discord.Interaction, guild_id: int) -> discord.Guild | None:
    """The server an answer in a private message is about (after answering Discord). Not
    cached here when another process serves it: looked up from Discord instead."""
    guild = interaction.client.get_guild(guild_id)
    if guild is not None:
        return guild
    try:
        return await interaction.client.fetch_guild(guild_id)
    except (discord.NotFound, discord.Forbidden):  # 403 once DMbot was removed from it
        await _say(interaction, SERVER_GONE)
    except discord.HTTPException:
        await _say(interaction, NOT_HERE)
    return None


async def _end_message(interaction: discord.Interaction, text: str) -> None:
    """Replace the message with what happened (its buttons go). If it can't be edited,
    say it in a new private message: what was saved is never left unsaid."""
    try:
        await interaction.edit_original_response(content=text, view=None, allowed_mentions=NO_PINGS)
    except discord.HTTPException:
        with contextlib.suppress(discord.HTTPException):
            await _say(interaction, text)


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
        accepted = ACCEPTED.format(
            campaign=md(campaign.name), server=md(guild.name), owner=md(offer.from_name)
        )
        if offer.status == "accepted":  # pressed again: say it again, if it's still theirs
            mine = campaign.owner_user_id == me
            await _end_message(interaction, accepted if mine else ENDED.format(days=HANDOVER_DAYS))
            return
        if not offer.is_open(now):  # taken back, declined or out of time: nothing to check
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        try:
            await guild.fetch_member(me)  # no members intent: ask Discord
        except discord.NotFound:
            await _say(interaction, NOT_A_MEMBER)
            return
        except discord.HTTPException:  # Discord couldn't say: never accept unchecked
            await _say(interaction, NOT_HERE)
            return
        result = await store.accept_handover(self.guild_id, self.offer_id, me, now)
        if result == "no_free_slot":
            deadline = discord.utils.format_dt(_at(offer.expires_at), "f")
            await _say(interaction, NO_FREE_SLOT.format(deadline=deadline))
        elif result == "gone":
            # Pressed twice at once: the other press may have accepted it just now.
            again = await store.get_offer(self.guild_id, self.offer_id, _now())
            done = again is not None and again.status == "accepted" and again.to_user_id == me
            await _end_message(interaction, accepted if done else ENDED.format(days=HANDOVER_DAYS))
        else:
            await _end_message(interaction, accepted)
            names = {"name": md(offer.to_name), "campaign": md(campaign.name)}
            await _tell_person(guild, offer.from_user_id, TOLD_ACCEPTED.format(**names))
            await _tell_screen(interaction, campaign, SCREEN_ACCEPTED.format(**names))


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
        if not offer.is_open(now):
            await _end_message(interaction, ENDED.format(days=HANDOVER_DAYS))
            return
        if await store.decline_handover(self.guild_id, self.offer_id, me, now) == "gone":
            # Pressed twice at once: the other press may have declined it just now.
            again = await store.get_offer(self.guild_id, self.offer_id, _now())
            done = again is not None and again.status == "declined" and again.to_user_id == me
            await _end_message(interaction, declined if done else ENDED.format(days=HANDOVER_DAYS))
            return
        await _end_message(interaction, declined)
        if campaign is not None:
            names = {"name": md(offer.to_name), "campaign": md(campaign.name)}
            await _tell_person(guild, offer.from_user_id, TOLD_DECLINED.format(**names))


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
            await self._ended(interaction, campaign)
            return
        if offer.from_user_id != interaction.user.id:
            await _say(interaction, NOT_YOUR_OFFER.format(owner=md(offer.from_name)))
            return
        if (
            await store.withdraw_handover(self.guild_id, self.offer_id, offer.from_user_id, now)
            == "gone"
        ):
            await self._ended(interaction, campaign)
            return
        await redraw_card(interaction, campaign)
        await _say(interaction, WITHDRAWN.format(campaign=md(campaign.name)))
        await _tell_person(
            guild,
            offer.to_user_id,
            TOLD_WITHDRAWN.format(owner=md(offer.from_name), campaign=md(campaign.name)),
        )

    async def _ended(self, interaction: discord.Interaction, campaign: Campaign | None) -> None:
        """The offer ended meanwhile: say so, and redraw the card without its stale
        button (it may have been answered, so the campaign is read again)."""
        if campaign is not None:
            fresh = await _store(interaction).get(self.guild_id, campaign.id)
            if fresh is not None:
                await redraw_card(interaction, fresh)
        await _say(interaction, ENDED.format(days=HANDOVER_DAYS))


async def redraw_card(interaction: discord.Interaction, campaign: Campaign) -> None:
    """⚙️ Settings as the campaign is now, with no offer waiting (best effort)."""
    if _settings_card is None:  # settings.py sets it on import: a bug if it's missing
        log.warning("No ⚙️ Settings card to redraw after a hand-over offer")
        return
    text, view = _settings_card(campaign, interaction.user.id)
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(content=text, view=view, allowed_mentions=NO_PINGS)


# ---- /dmbot start on a campaign with no owner yet --------------------------------------


def take_on_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TakeOnButton(campaign_id))
    view.add_item(NotNowButton(campaign_id))
    return view


def take_on_only_view(campaign_id: str) -> discord.ui.View:
    """Just the Take it on button, for a start that was refused for want of an owner."""
    view = discord.ui.View(timeout=None)
    view.add_item(TakeOnButton(campaign_id))
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
    """Take it on: after `/dmbot start`, and on ⚙️ Settings of a campaign with no owner."""

    def __init__(self, campaign_id: str, *, row: int | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=TAKE_ON_LABEL,
                emoji="🤝",
                style=discord.ButtonStyle.primary,
                row=row,
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
        me = interaction.user.id
        result = await store.take_ownership(guild.id, self.campaign_id, me, _now())
        if result == "no_free_slot":
            await _say(interaction, TAKE_NO_ROOM)
        elif result == "gone":
            # Pressed again (or twice at once): say "yours" only if it really is.
            fresh = await store.get(guild.id, self.campaign_id)
            mine = fresh is not None and fresh.owner_user_id == me
            await _end_message(
                interaction, TAKEN.format(campaign=md(campaign.name)) if mine else TAKE_GONE
            )
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
