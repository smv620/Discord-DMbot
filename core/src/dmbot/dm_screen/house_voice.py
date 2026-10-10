"""A house rule said at the table, offered on the DM screen (#953).

When a campaign's DM says "house rule: …" (see `dmbot.rules.house_voice`), a proposal goes
on the DM screen, never in the transcript and never to players: the words heard, and three
buttons. **Save** writes it down as the next house rule ("Said at the table, <date>"),
**Edit** opens the Add a house rule form with the words filled in, **Cancel** takes the
buttons away. If an existing house rule is about the same spell, condition or creature
(the same names the rules cards look for), both are shown and the buttons are **Keep
both**, **Replace rule N** and **Cancel**. Nothing is ever saved unless a DM presses a
button; every press checks again that the person is one of the campaign's DMs.

The proposals live with the running session, so after a restart, or once the session is
over, a press just says it is closed. Register with
`bot.add_dynamic_items(HouseVoiceButton)`.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Sequence
from typing import Any

import discord

from dmbot.dm_screen.rules_cards import spotter_for
from dmbot.rules import house
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_voice import Clash, Proposal, Said
from dmbot.ui import rule_card

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
CLOSED = (
    "This offer has ended (the session is over, or DMbot restarted). To add the rule, press "
    "**Add a house rule** in `/dmbot houserules`."
)
ONLY_DMS = "Only this campaign's DMs can use these buttons."
FAILED = (
    "Couldn't save that. Nothing was changed. Press the button again, or use `/dmbot houserules`."
)
NOT_SAVED = (
    "_Nothing is saved unless you press **Save**. Everyone in the server can read house rules._"
)
KEEPS_ALL = "**Save as new rule** keeps them all. To change one, use `/dmbot houserules`."
NOT_SAVED_CLASH = (
    "_Nothing is saved unless you press a button. Everyone in the server can read house rules._"
)
UNCHECKED = "_DMbot couldn't look at your house rules just now, so it didn't check for a repeat._"
EXPIRES = "_These buttons stop working when the session ends._"
CLASH_SHOWN = 2  # most existing rules named under a proposal
CLASH_LINE_MAX = 120
LABELS = {
    "save": ("Save rule", "✅", discord.ButtonStyle.primary),
    "edit": ("Edit", "✏️", discord.ButtonStyle.secondary),
    "cancel": ("Cancel", "✖️", discord.ButtonStyle.secondary),
    "both": ("Save as new rule", "✅", discord.ButtonStyle.primary),
    "replace": ("Replace rule {n}", "🔁", discord.ButtonStyle.danger),
}


def scenario_for(now: float, *, typed: bool = False) -> str:
    """What the house rule says about where it came from."""
    how = "Typed by the DM" if typed else "Said at the table"
    return f"{how}, " + time.strftime("%Y-%m-%d", time.gmtime(now))


def clashes(
    said: Said, rules: Sequence[HouseRule], target: str, fallback: str
) -> tuple[Clash, ...]:
    """The existing house rules that name the same spell, condition or creature as the new
    words, lowest number first: the simple kind of conflict (no AI)."""
    found: dict[int, HouseRule] = {}
    for mention in spotter_for(target, fallback).find(said.rule):
        names = rule_card.names_for(mention.entry, mention.said)
        for rule in rule_card.house_matches(rules, names):
            found[rule.number] = rule
    return tuple(Clash(r.number, r.version, r.rule) for _, r in sorted(found.items()))


def _md(text: str, limit: int) -> str:
    return rule_card._fit(discord.utils.escape_markdown(text), limit)


def proposal_text(proposal: Proposal) -> str:
    said = proposal.said
    lines = [f"🏠 **Save as a house rule?** You said: “{_md(said.rule, 600)}”"]
    if said.cut:
        lines.append("_Only the first 500 characters fit; the rest is left out. Press **Edit**._")
    if proposal.clashes:
        for clash in proposal.clashes[:CLASH_SHOWN]:
            lines.append(f"House rule {clash.number} mentions the same thing: "
                         f"“{_md(clash.words, CLASH_LINE_MAX)}”")  # fmt: skip
        if len(proposal.clashes) > CLASH_SHOWN:
            lines.append(f"…and {len(proposal.clashes) - CLASH_SHOWN} more: `/dmbot houserules`")
        if len(proposal.clashes) == 1:
            n = proposal.clashes[0].number
            lines.append(
                f"**Save as new rule** keeps both. **Replace rule {n}** swaps its words for these."
            )
        else:
            lines.append(
                "**Save as new rule** keeps them all. To change one, use `/dmbot houserules`."
            )
        lines.append(NOT_SAVED_CLASH)
    else:
        if proposal.unchecked:
            lines.append(UNCHECKED)
        lines.append(NOT_SAVED)
    lines.append(EXPIRES)
    return "\n".join(lines)


def proposal_view(guild_id: int, proposal_id: str, proposal: Proposal) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if not proposal.clashes:
        actions = ["save", "edit", "cancel"]
    elif len(proposal.clashes) == 1:  # replacing is offered only when it is clear what goes
        actions = ["both", "replace", "cancel"]
    else:
        actions = ["both", "edit", "cancel"]
    for action in actions:
        number = proposal.clashes[0].number if proposal.clashes else 0
        view.add_item(HouseVoiceButton(guild_id, proposal_id, action, number))
    return view


def new_id() -> str:
    return uuid.uuid4().hex[:8]


class HouseVoiceButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=(
        r"dmbot:hrv:(?P<action>save|edit|cancel|both|replace):(?P<guild>[0-9]{1,20}):"
        r"(?P<proposal>[0-9a-f]{8})"
    ),
):
    def __init__(self, guild_id: int, proposal_id: str, action: str, number: int = 0) -> None:
        label, emoji, style = LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label.format(n=number),
                emoji=emoji,
                style=style,
                custom_id=f"dmbot:hrv:{action}:{guild_id}:{proposal_id}",
            )
        )
        self.guild_id, self.proposal_id, self.action = guild_id, proposal_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> HouseVoiceButton:
        return cls(int(match["guild"]), match["proposal"], match["action"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.guild_id, self.proposal_id, self.action)
        except Exception:
            log.exception("A house rule proposal button failed")
            if interaction.response.is_done():
                await interaction.followup.send(FAILED, ephemeral=True)
            else:
                await interaction.response.send_message(FAILED, ephemeral=True)


async def _finish(interaction: discord.Interaction, note: str) -> None:
    """Take the buttons off the proposal and say what happened to it."""
    message = interaction.message
    words = message.content if message is not None else ""
    await interaction.response.edit_message(content=f"{words}\n{note}", view=None)


async def press(
    interaction: discord.Interaction, guild_id: int, proposal_id: str, action: str
) -> None:
    """What a button does. The session and the person are checked again every time."""
    bot: Any = interaction.client
    table = bot.tables.get(guild_id) if interaction.guild_id == guild_id else None
    proposal: Proposal | None = (
        table.house_voice.proposals.get(proposal_id) if table is not None else None
    )
    if table is None or proposal is None or table.campaign_id is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    campaign = await bot.campaigns.get(guild_id, table.campaign_id)
    if campaign is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return
    if action == "cancel":
        if table.house_voice.proposals.pop(proposal_id, None) is None:
            await interaction.response.send_message(CLOSED, ephemeral=True)
            return
        await _finish(interaction, "_Cancelled. Nothing was saved._")
        return
    if action == "edit":
        from dmbot.ui.house_rules import ProposalForm  # (that module imports this one's peers)

        await interaction.response.send_modal(ProposalForm(campaign, proposal, proposal_id))
        return
    store = bot.house_rules
    if store is None:
        await interaction.response.send_message(FAILED, ephemeral=True)
        return
    # Claimed before the first await, so a second press (or another DM) finds it gone and
    # nothing is saved twice; given back if the save is refused.
    if table.house_voice.proposals.pop(proposal_id, None) is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    try:
        if action == "replace":
            note = await _replace(store, campaign, interaction.user.id, proposal)
        else:  # "save" and "both"
            saved = await store.add(
                campaign.guild_id,
                campaign.id,
                interaction.user.id,
                proposal.said.rule,
                None,
                scenario=proposal.scenario,
                session_id=proposal.session_id,
            )
            note = f"✅ Saved as house rule {saved.number}."
    except BaseException as exc:
        table.house_voice.proposals[proposal_id] = proposal
        if isinstance(exc, HouseRuleError):
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        raise
    await _finish(interaction, note)
    from dmbot.ui import house_file as file_ui  # (the UI modules import this one's peers)

    await file_ui.send_after_change(interaction, campaign, note)


async def _replace(store: Any, campaign: Any, user_id: int, proposal: Proposal) -> str:
    """The new words in place of the clashing rule, which keeps its number and its
    "instead of". Refused if another DM changed that rule since the proposal."""
    clash = proposal.clashes[0]
    current = next(
        (r for r in await store.list(campaign.guild_id, campaign.id) if r.number == clash.number),
        None,
    )
    if current is None:
        raise HouseRuleError(house.GONE)
    changed = await store.edit(
        campaign.guild_id,
        campaign.id,
        user_id,
        clash.number,
        proposal.said.rule,
        current.supersedes,
        unchanged_since=clash.version,
    )
    return (
        f"🔁 House rule {changed.number} now says this. "
        f"It used to say: “{_md(current.rule, CLASH_LINE_MAX)}”"
    )
