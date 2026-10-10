"""⏳ Timed effects on the DM screen (#998; docs/PLAN.md, "TimeBot"): the DM starts a timer for
a spell or effect (from the clock message's **Start a timer**, or **Time it** on a rules card),
and when the game clock passes its end DMbot says once that it has likely ended, with
**Ended** and **Still going +10 min**. Nothing starts by itself and nothing ends silently: the
DM decides. Only the campaign's DMs can use any of it (checked in the database transaction).

Register with `bot.add_dynamic_items(EffectButton)`.
"""

from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import CampaignError
from dmbot.rules import index
from dmbot.timebot import durations
from dmbot.timebot.effects import EXTRA_MINUTES, Effect

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
_ID = r"(?P<campaign>[0-9a-f]{32})"
FAILED = "Sorry, that didn't save. Please try again."
NEED_LENGTH = "Give a length, like 10 minutes or 1 hour. DMbot has no length for that name."
NOT_TIMED = (
    "{name} isn't timed ({how}), so there is nothing to start. Give a length to time it anyway."
)
STARTED = "⏳ Timer started: {line}"


def spell_duration(campaign: Campaign, name: str) -> durations.Duration | None:
    """What the free rules say about this name's duration, in the campaign's own rulesets
    (newest first), or None if it isn't a spell DMbot knows."""
    hit = index.srd().lookup(name, campaign.target_ruleset, campaign.fallback_ruleset, kind="spell")
    if hit is None:
        return None
    return durations.parse(str(hit.entry.details.get("duration", "")))


class TimerForm(discord.ui.Modal):
    name_box: discord.ui.TextInput[TimerForm] = discord.ui.TextInput(
        label="Spell or effect", placeholder="Bless", max_length=60
    )
    who_box: discord.ui.TextInput[TimerForm] = discord.ui.TextInput(
        label="Who is it on? (can be empty)", placeholder="Mira", required=False, max_length=60
    )
    length_box: discord.ui.TextInput[TimerForm] = discord.ui.TextInput(
        label="How long? (empty: the spell's own)",
        placeholder="1 minute, 10 minutes, 1 hour",
        required=False,
        max_length=30,
    )

    def __init__(self, campaign_id: str, name: str = "", length: str = "") -> None:
        super().__init__(title="Start a timer")
        self.campaign_id = campaign_id
        self.name_box.default = name or None
        self.length_box.default = length or None

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await start_timer(
                interaction,
                self.campaign_id,
                self.name_box.value,
                self.who_box.value,
                self.length_box.value,
            )
        except Exception:
            log.exception("Starting a timer failed")
            await _reply(interaction, FAILED)


async def start_timer(
    interaction: discord.Interaction, campaign_id: str, name: str, who: str, length: str
) -> None:
    """Start the timer the form asked for (the form was answered already)."""
    client: Any = interaction.client
    guild = interaction.guild
    campaign = await client.campaigns.get(guild.id, campaign_id) if guild else None
    if campaign is None:
        await _reply(interaction, "That campaign isn't here any more.")
        return
    typed = durations.parse(length) if length.strip() else None
    known = spell_duration(campaign, name)
    chosen = typed if typed is not None else known
    if chosen is None or not chosen.timed:
        if length.strip():
            await _reply(interaction, NEED_LENGTH)
        elif known is not None:
            await _reply(
                interaction, NOT_TIMED.format(name=name.strip(), how="it has no set length")
            )
        else:
            await _reply(interaction, NEED_LENGTH)
        return
    assert chosen.minutes is not None
    concentration = chosen.concentration or bool(known and known.concentration and typed is None)
    try:
        effect = await client.effects.start(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            name,
            who,
            chosen.minutes,
            concentration,
        )
    except CampaignError as exc:
        await _reply(interaction, str(exc))
        return
    stored = await client.clocks.get(campaign.guild_id, campaign.id)
    if stored is not None:
        from dmbot.dm_screen import clock as clock_screen

        await clock_screen.show(client, campaign, stored)
        await _reply(interaction, STARTED.format(line=effect.line(stored.clock.minute)))


async def announce_due(client: Any, campaign: Campaign, minute: int) -> None:
    """One line per timer the clock has just passed, with its two buttons. Each is said once
    (`claim_due` marks it), and only on the DM screen."""
    store = getattr(client, "effects", None)
    if store is None or campaign.dm_screen_channel_id is None:
        return
    try:
        due = await store.claim_due(campaign.guild_id, campaign.id, minute)
    except Exception:
        log.exception("Couldn't look for timers that have ended")
        return
    for effect in due:
        view = discord.ui.View(timeout=None)
        view.add_item(EffectButton(campaign.id, effect.number, "end"))
        view.add_item(EffectButton(campaign.id, effect.number, "more"))
        await client.post_message(campaign.dm_screen_channel_id, effect.ended_line(), view)


class EffectButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=rf"dmbot:fx:{_ID}:(?P<number>[0-9]{{1,6}}):(?P<action>end|more)",
):
    """**Ended** takes the timer away; **Still going +10 min** runs it ten game minutes more."""

    def __init__(self, campaign_id: str, number: int, action: str) -> None:
        end = action == "end"
        super().__init__(
            discord.ui.Button(
                label="Ended" if end else f"Still going +{EXTRA_MINUTES} min",
                emoji="✅" if end else "⏳",
                style=discord.ButtonStyle.primary if end else discord.ButtonStyle.secondary,
                custom_id=f"dmbot:fx:{campaign_id}:{number}:{action}",
            )
        )
        self.campaign_id, self.number, self.action = campaign_id, number, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> EffectButton:
        return cls(match["campaign"], int(match["number"]), match["action"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        client: Any = interaction.client
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer()
        try:
            campaign = await client.campaigns.get(guild.id, self.campaign_id)
            if campaign is None:
                await _reply(interaction, "That campaign isn't here any more.")
                return
            if self.action == "end":
                effect: Effect = await client.effects.end(
                    guild.id, campaign.id, interaction.user.id, self.number
                )
                note = f"✅ {effect.name} ended."
            else:
                effect = await client.effects.extend(
                    guild.id, campaign.id, interaction.user.id, self.number
                )
                note = f"⏳ {effect.name} goes on {EXTRA_MINUTES} more minutes."
        except CampaignError as exc:
            await _reply(interaction, str(exc))
            return
        except Exception:
            log.exception("A timer button failed")
            await _reply(interaction, FAILED)
            return
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(content=note, view=None)
        stored = await client.clocks.get(guild.id, campaign.id)
        if stored is not None:
            from dmbot.dm_screen import clock as clock_screen

            await clock_screen.show(client, campaign, stored)


async def _reply(interaction: discord.Interaction, text: str) -> None:
    with contextlib.suppress(discord.HTTPException):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True)
