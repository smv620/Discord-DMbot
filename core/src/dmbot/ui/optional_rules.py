"""`/dmbot optionalrules`: turn rules from other books on or off for a campaign (#49).

The campaign's DMs and server managers only, in a private reply. Each change is saved
at once (`CampaignStore.set_optional_rule`) and noted in the DM screen. DMbot's rules
checks will follow these choices once they exist (Phase 3); house rules still win.
"""

from __future__ import annotations

import logging

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import optional
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
    _bot,
    _is_manager,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
    dmbot_group,
)

log = logging.getLogger(__name__)

TEXT_MAX = 1900  # under Discord's 2,000 characters


async def _campaign_for(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if this person may change its rules; tells them otherwise."""
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return None
    campaign = await _bot(interaction).campaigns.get(guild.id, campaign_id)
    if campaign is None or not logic.can_run(
        campaign, interaction.user.id, _is_manager(interaction)
    ):
        await _tell(interaction, logic.NO_CAMPAIGN_ACCESS)
        return None
    return campaign


def rules_text(campaign: Campaign, overrides: dict[str, bool]) -> str:
    """The list: ✅ on, ⬜ off, each with what it does and where it's from."""
    main = logic.ruleset_label(campaign.target_ruleset)
    lines = [
        f"📚 **Rules from other books: {campaign.name}**",
        f"These add to the main rules ({main}) where they don't already cover something. "
        "Your own house rules always win. Pick one below to turn it on or off.",
    ]
    for r in optional.applying(campaign.target_ruleset):
        on = optional.is_on(r.id, overrides, campaign.optional_rules_default)
        lines.append(f"{'✅' if on else '⬜'} **{r.name}**: {r.summary} _({r.source})_")
    if len(lines) == 2:
        lines.append("None of the rules DMbot knows add to these main rules.")
    return "\n".join(lines)[:TEXT_MAX]


class OptionalRulesMenu(_Menu):
    def __init__(self, campaign: Campaign, overrides: dict[str, bool]) -> None:
        super().__init__()
        self.campaign_id = campaign.id
        rules = optional.applying(campaign.target_ruleset)[: logic.SELECT_OPTIONS_MAX]
        if not rules:
            return
        self.pick = _Select(
            self._toggle,
            placeholder="Turn a rule on or off…",
            options=[
                discord.SelectOption(
                    label=r.name,
                    value=r.id,
                    description="On: tap to turn off"
                    if optional.is_on(r.id, overrides, campaign.optional_rules_default)
                    else "Off: tap to turn on",
                )
                for r in rules
            ],
        )
        self.add_item(self.pick)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        chosen = optional.rule(self.pick.values[0])
        if chosen is None:
            await _tell(interaction, "DMbot doesn't know that rule any more. Open the list again.")
            return
        store = _bot(interaction).campaigns
        overrides = await store.optional_rule_overrides(campaign.guild_id, campaign.id)
        turn_on = not optional.is_on(chosen.id, overrides, campaign.optional_rules_default)
        await store.set_optional_rule(campaign.guild_id, campaign.id, chosen.id, turn_on)
        overrides[chosen.id] = turn_on
        self.stop()
        view = OptionalRulesMenu(campaign, overrides)
        await _replace(interaction, rules_text(campaign, overrides), view)
        if campaign.dm_screen_channel_id is not None:  # the DM screen keeps a record
            await _bot(interaction).post(
                campaign.dm_screen_channel_id,
                f"Optional rule {'on' if turn_on else 'off'}: {chosen.name}.",
            )


async def show_rules(interaction: discord.Interaction, campaign: Campaign) -> None:
    overrides = await _bot(interaction).campaigns.optional_rule_overrides(
        campaign.guild_id, campaign.id
    )
    await _send(
        interaction, rules_text(campaign, overrides), OptionalRulesMenu(campaign, overrides)
    )


class CampaignChoice(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        now = int(discord.utils.utcnow().timestamp())
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(
                    label=logic.name_label(c.name),
                    value=c.id,
                    description=logic.option_description(c, now),
                )
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.pick.values[0])
        if campaign is not None:
            self.stop()
            await show_rules(interaction, campaign)


@dmbot_group.command(
    name="optionalrules", description="Turn rules from other books on or off for a campaign"
)
async def dmbot_optional_rules(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    playing = bot.active_campaign_id(guild.id)
    current = next((c for c in mine if c.id == playing), None)
    if current is None and len(mine) == 1:
        current = mine[0]
    if current is not None:
        await show_rules(interaction, current)
    elif not mine:
        await _tell(
            interaction, "You're not the DM of any campaign here. Use `/dmbot start` to set one up."
        )
    else:
        await _send(interaction, "**Which campaign's rules?**", CampaignChoice(mine))
