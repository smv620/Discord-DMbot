"""`/dmbot optionalrules`: turn optional rules from other books on or off (#49).

The campaign's DMs and server managers only, in a private reply. Each change is saved
at once (`CampaignStore.set_optional_rule`) and noted in the DM screen. DMbot's rules
checks will follow these choices once they exist (Phase 3); house rules still win.
"""

from __future__ import annotations

import logging

import discord

from dmbot.campaigns import RULESETS, Campaign, CampaignError
from dmbot.rules import optional
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
    _bot,
    _is_manager,
    _Menu,
    _now,
    _replace,
    _Select,
    _send,
    _tell,
    dmbot_group,
)

log = logging.getLogger(__name__)

TEXT_MAX = 1900  # under Discord's 2,000 characters
GONE = "That campaign isn't here any more. Use `/dmbot optionalrules` to see the list again."
NO_SCREEN_NOTE = (
    "DMbot couldn't post this to your DM screen. Make sure DMbot can still see that "
    "channel, or run `/dmbot start` to set it up again."
)
COVERED_2024 = (
    "Not listed, because the 2024 rules have their own version: tying knots, tools, "
    "spotting a spell, how fast you fall, customizing your origin, extra class features."
)


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


def _main_rules(target: str) -> str:
    """The main rules' name without "(newest)", which reads oddly mid-sentence."""
    return f"{target} rules" if target in RULESETS else logic.ruleset_label(target)


def rules_text(campaign: Campaign, overrides: dict[str, bool], news: str = "") -> str:
    """The list, grouped by book: ✅ on, ⬜ off, and what each rule does. The catalog
    lists each book's rules together, so each book gets one heading. `news` (what just
    changed) goes first, where a phone shows it."""
    main = _main_rules(campaign.target_ruleset)
    rules = optional.applying(campaign.target_ruleset)
    lines = [news] if news else []
    lines.append(f"📚 **Optional rules: {campaign.name}**")
    if not rules:
        lines.append(f"DMbot has no optional rules to offer for the {main} yet.")
    else:
        lines.append(
            f"Rules from Xanathar's and Tasha's that add to your main rules ({main}). "
            "✅ on · ⬜ off. Pick a rule in the menu below to switch it. "
            "Your house rules always win."
        )
    book = ""
    for r in rules:
        if r.source != book:
            book = r.source
            lines.append(f"**{book}**")
        on = optional.is_on(r.id, overrides, campaign.optional_rules_default)
        lines.append(f"{'✅' if on else '⬜'} **{r.name}**: {r.summary}")
    tail = []
    if rules:  # nothing to keep on otherwise
        if campaign.target_ruleset == "2024":
            tail.append(COVERED_2024)
        tail.append(
            "DMbot doesn't check rules yet. When it does, it will use only the optional "
            "rules you keep on here. You make every call at the table."
        )
    text = "\n".join(lines)
    room = TEXT_MAX - sum(len(t) + 1 for t in tail)
    if len(text) > room:  # never cut a line in half; the menu still lists them all
        cut = text.rfind("\n", 0, room - 2)
        text = (text[:cut] if cut > 0 else text[: room - 2]) + "\n…"
    return "\n".join([text, *tail])


class OptionalRulesMenu(_Menu):
    def __init__(self, campaign: Campaign, overrides: dict[str, bool]) -> None:
        super().__init__()
        self.campaign_id = campaign.id
        rules = optional.applying(campaign.target_ruleset)[: logic.SELECT_OPTIONS_MAX]
        if not rules:
            return
        options = []
        for r in rules:
            on = optional.is_on(r.id, overrides, campaign.optional_rules_default)
            # The value says what this choice does, so an old or shared menu never flips a
            # rule the other way, and picking it twice changes nothing.
            options.append(
                discord.SelectOption(
                    label=r.name,
                    value=f"{r.id}:{'off' if on else 'on'}",
                    description="✅ On now · pick to turn off"
                    if on
                    else "⬜ Off now · pick to turn on",
                )
            )
        self.pick = _Select(self._switch, placeholder="Switch a rule on or off…", options=options)
        self.add_item(self.pick)

    async def _switch(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        rule_id, _, wanted = self.pick.values[0].rpartition(":")
        chosen = optional.rule(rule_id)
        if chosen is None or chosen not in optional.applying(campaign.target_ruleset):
            await _tell(
                interaction,
                "That rule isn't in this campaign's list any more. Use `/dmbot optionalrules` "
                "to see the list again.",
            )
            return
        turn_on = wanted == "on"
        store = _bot(interaction).campaigns
        try:
            await store.set_optional_rule(campaign.guild_id, campaign.id, chosen.id, turn_on)
            overrides = await store.optional_rule_overrides(campaign.guild_id, campaign.id)
        except CampaignError:
            await _tell(interaction, GONE)
            return
        self.stop()
        # The DM screen keeps a record. Noted before the reply, so a failed reply can't
        # leave a saved change unrecorded.
        noted = True
        if campaign.dm_screen_channel_id is not None:
            noted = await _bot(interaction).post(
                campaign.dm_screen_channel_id,
                f"{'✅' if turn_on else '⬜'} Optional rule turned {'on' if turn_on else 'off'}: "
                f"**{chosen.name}** (by {interaction.user.mention})",
            )
        news = f"{'✅' if turn_on else '⬜'} **{chosen.name}** is now {'on' if turn_on else 'off'}."
        if not noted:
            news += " " + NO_SCREEN_NOTE
        try:
            await _replace(
                interaction,
                rules_text(campaign, overrides, news),
                OptionalRulesMenu(campaign, overrides),
            )
        except discord.HTTPException:  # the menu message is gone: say what was saved
            log.warning("Couldn't update the optional rules menu", exc_info=True)
            try:
                await _tell(interaction, f"Saved. {news}")
            except discord.HTTPException:  # too late to answer: the change is saved anyway
                log.warning("Couldn't tell the DM an optional rule was saved", exc_info=True)


async def show_rules(interaction: discord.Interaction, campaign: Campaign) -> None:
    try:
        overrides = await _bot(interaction).campaigns.optional_rule_overrides(
            campaign.guild_id, campaign.id
        )
    except CampaignError:
        await _tell(interaction, GONE)
        return
    await _send(
        interaction, rules_text(campaign, overrides), OptionalRulesMenu(campaign, overrides)
    )


class CampaignChoice(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        now = _now()
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
    name="optionalrules", description="Turn optional rules from Xanathar's and Tasha's on or off"
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
        await _send(interaction, "**Which campaign's optional rules?**", CampaignChoice(mine))
