"""A table's suggestion for a shared house-rules set (#1259, #1191 part 2b).

A DM of a campaign that follows a set can send one of its own house rules to the set's
publisher ("💡 Suggest to <set>"). It lands on the publisher's DM screen at once as one note:
"A table that uses **<set> house rules** suggests: <rule> [See] [Add to set] [No thanks]".

**The note never says which campaign, server or DM sent it.** Only rules cross between
campaigns (CLAUDE.md, isolation), and the publisher's side of the database holds no sender
either (see `RULE_SUGGESTIONS` in the schema). Nothing changes until a DM of the publishing
campaign presses Add to set, which makes the next version of the set through the same checks as any
other change (paid plan, a DM, the version they saw). The sender hears the outcome in one line at
its next session start. Buttons survive a restart (`bot.add_dynamic_items(SuggestionButton)`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.logs import set_log_context
from dmbot.rules import rule_sets, set_diff
from dmbot.rules.house import HouseRuleError
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell

log = logging.getLogger(__name__)

ONLY_DMS = "Only this campaign's DMs can answer this."
GONE = "That campaign isn't here any more."
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
ANSWERED = "Someone already answered this suggestion."
EXPIRED = "That suggestion is too old to add. The table was told it can send it again."
TEXT_MAX = MESSAGE_MAX - 200  # margin kept from before: room for the words a sender adds around it
SHOWN_MAX = 120  # a rule quoted in a one-line message

_LABELS = {
    "see": ("See", discord.ButtonStyle.secondary),
    "accept": ("Add to set", discord.ButtonStyle.primary),
    "no": ("No thanks", discord.ButtonStyle.secondary),
}


def _words(rule: str, instead: str | None, replaces: bool = False, limit: int = 400) -> str:
    text = discord.utils.escape_markdown(rule)
    if instead:
        what = "replaces this set rule" if replaces else "instead of"
        text += f" ({what}: {discord.utils.escape_markdown(instead)})"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def note_text(set_name: str, rule: str, instead: str | None, replaces: bool = False) -> str:
    """The publisher's note: the words and the set, nothing about the sender."""
    name = discord.utils.escape_markdown(set_name)
    return (
        f"💡 A table using **{name} house rules** suggests (sender not shown):\n"
        f"{_words(rule, instead, replaces)}\nAdd to set puts it in for every table that uses it."
    )


def note_view(campaign_id: str, suggestion_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in _LABELS:
        view.add_item(SuggestionButton(campaign_id, action, suggestion_id))
    return view


class SuggestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:sugg:(?P<action>see|accept|no):(?P<campaign>[0-9a-f]{32}):(?P<sid>[0-9a-f]{32})",
):
    def __init__(self, campaign_id: str, action: str, suggestion_id: str) -> None:
        label, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:sugg:{action}:{campaign_id}:{suggestion_id}",
            )
        )
        self.campaign_id, self.action, self.suggestion_id = campaign_id, action, suggestion_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SuggestionButton:
        return cls(match["campaign"], match["action"], match["sid"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.action, self.suggestion_id)
        except Exception:
            log.exception("A suggestion button failed")
            await _tell(interaction, COULDNT)


async def _close_note(interaction: discord.Interaction, text: str) -> None:
    """Take the buttons off the note and say how it ended, so it can't be pressed twice."""
    message = interaction.message
    if message is None:
        return
    try:
        await message.edit(content=text, view=None)
    except discord.HTTPException:
        log.warning("Couldn't close a suggestion note", exc_info=True)


async def press(
    interaction: discord.Interaction, campaign_id: str, action: str, suggestion_id: str
) -> None:
    await _answer_first(interaction)
    bot = _bot(interaction)
    sets = bot.rule_sets
    if interaction.guild is None or sets is None:
        await _tell(interaction, COULDNT)
        return
    campaign = await bot.campaigns.get(interaction.guild.id, campaign_id)
    if campaign is None:
        await _tell(interaction, GONE)
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return
    asked = await sets.suggestion(campaign.guild_id, campaign.id, suggestion_id)
    if asked is not None and asked.status == "expired":
        await _tell(interaction, EXPIRED)
        return
    if asked is None or asked.status != "open":
        await _tell(interaction, ANSWERED)
        return
    if action == "see":
        await _see(interaction, campaign, asked)
    elif action == "no":
        try:
            done = await sets.decline_suggestion(
                campaign.guild_id, campaign.id, interaction.user.id, suggestion_id
            )
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        if not done:
            await _tell(interaction, ANSWERED)
            return
        await _close_note(
            interaction,
            f"{interaction.message.content if interaction.message else ''}\n"
            "↩️ No thanks. Nothing changed.",
        )
    else:
        await _accept(interaction, campaign, asked)


async def _see(
    interaction: discord.Interaction, campaign: Campaign, asked: rule_sets.Suggestion
) -> None:
    """What the set would look like with it: the one line that would be added or changed."""
    sets = _bot(interaction).rule_sets
    assert sets is not None  # checked by the caller
    shared = await sets.current_content(campaign.guild_id, campaign.id)
    if shared is None:
        await _tell(interaction, rule_sets.NOT_SHARED)
        return
    mine, before = shared
    try:
        after = rule_sets.suggested_content(before, asked.rule, asked.instead)
    except rule_sets.RuleSetError as exc:
        await _tell(interaction, str(exc))
        return
    lines = [set_diff.describe(c, 300) for c in set_diff.diff(before, after)]
    name = discord.utils.escape_markdown(mine.name[:60])
    await _tell(
        interaction,
        f"If you press Add to set, **{name}** becomes version {mine.version + 1}:\n"
        + fit_message("\n".join(lines), limit=TEXT_MAX),
    )


async def _accept(
    interaction: discord.Interaction, campaign: Campaign, asked: rule_sets.Suggestion
) -> None:
    bot = _bot(interaction)
    sets = bot.rule_sets
    assert sets is not None  # checked by the caller
    refused = await bot.plan_gate("share", campaign.guild_id, campaign, interaction.user.id)
    if refused is not None:
        await _tell(interaction, refused)
        return
    try:
        # No expected version: the store adds the rule to whichever version is newest under its
        # lock, so a publish in between is never overwritten, and the DM saw one rule, not a set.
        made = await sets.accept_suggestion(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            asked.id,
            plan_ok=True,  # checked just above with plan_gate
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    name = discord.utils.escape_markdown(made.name[:60])
    done = f"✅ Added to **{name}**: it is now version {made.version}."
    await _close_note(
        interaction, f"{interaction.message.content if interaction.message else ''}\n{done}"
    )
    await _tell(interaction, "Tables using it get it at their next session start.")


async def post_note(
    bot: Any, guild_id: int, campaign_id: str, suggestion: rule_sets.Suggestion, set_name: str
) -> bool:
    """Put the note on the publishing campaign's DM screen. False if it could not be posted
    (it is tried again at that campaign's next session start)."""
    sets = bot.rule_sets
    try:
        campaign = await bot.campaigns.get(guild_id, campaign_id)
        if campaign is None or campaign.dm_screen_channel_id is None:
            return False
        replaces = False
        shared = await sets.current_content(guild_id, campaign_id)
        if shared is not None:  # say whether Add to set would replace one of its rules or add
            replaces = rule_sets.replaced_rule(shared[1]["rules"], suggestion.instead) is not None
        posted = await bot.post_message(
            campaign.dm_screen_channel_id,
            note_text(set_name, suggestion.rule, suggestion.instead, replaces),
            note_view(campaign_id, suggestion.id),
        )
        if posted is None:
            return False
        await sets.mark_noted(guild_id, campaign_id, suggestion.id)
        return True
    except Exception:
        log.exception("Couldn't post a suggestion note")
        return False


def send_note(bot: Any, guild_id: int, campaign_id: str, suggestion_id: str, set_name: str) -> None:
    """Post the note for a suggestion just sent, in a task of its own. The sender's button press
    tags its logs with the SENDER's server; the note is the publisher's business, so the task
    re-tags its logs with the publisher's server and campaign, and a failed post can never put
    the two in one log line."""

    async def apart() -> None:
        set_log_context(guild_id=guild_id, campaign_id=campaign_id)
        sets = bot.rule_sets
        found = await sets.suggestion(guild_id, campaign_id, suggestion_id)
        if found is not None:  # a failed post is tried again at the publisher's next session
            await post_note(bot, guild_id, campaign_id, found, set_name)

    bot.track(apart(), "suggestion-note")


async def post_waiting(bot: Any, campaign: Campaign) -> None:
    """At a session's start: notes that never reached the DM screen are posted now."""
    sets = bot.rule_sets
    if sets is None:
        return
    try:
        for suggestion, set_name in await sets.unnoted_suggestions(campaign.guild_id, campaign.id):
            if not await post_note(bot, campaign.guild_id, campaign.id, suggestion, set_name):
                return  # no screen, or Discord is down: the rest would fail the same way
    except Exception:
        log.exception("Couldn't post waiting suggestion notes")


def answer_line(sent: rule_sets.Sent) -> str:
    name = discord.utils.escape_markdown(sent.set_name)
    words = discord.utils.escape_markdown(sent.rule)
    if len(words) > SHOWN_MAX:
        words = words[: SHOWN_MAX - 1] + "…"
    if sent.status == "accepted":
        return f"💡 The **{name}** DM added your suggestion (now version {sent.version}): {words}"
    if sent.status == "present":
        return f"💡 The **{name}** DM already had that rule, so nothing was added: {words}"
    if sent.status == "gone":
        return f"💡 **{name}** isn't being shared any more, so your suggestion wasn't used: {words}"
    if sent.status == "expired":
        return f"💡 No answer yet from the **{name}** DM. You can send it again: {words}"
    return f"💡 The **{name}** DM said no thanks to your suggestion: {words}"


async def tell_answers(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """At a session's start: one line for each suggestion this campaign sent that has an answer.
    Never raises. Each is told once."""
    sets = bot.rule_sets
    if sets is None:
        return
    try:
        answered = [
            s for s in await sets.sent_answers(campaign.guild_id, campaign.id) if s.status != "open"
        ]
        if not answered:
            return
        if await bot.post(screen_id, "\n".join(answer_line(s) for s in answered)):
            await sets.mark_told(campaign.guild_id, campaign.id, [s.id for s in answered])
    except Exception:
        log.exception("Couldn't tell the answers to suggestions")
