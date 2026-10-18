"""A table's suggestion for a shared homebrew pack (#1537), as #1259 does for house-rules sets.

A DM of a campaign that follows a pack can send one of its own homebrew entries to the pack's
publisher ("💡 Suggest to <pack>"). It lands on the publisher's DM screen at once as one note:
"A table that uses **<pack> homebrew** suggests: <entry> [See] [Add to pack] [No thanks]".

**The note never says which campaign, server or DM sent it.** Only entries cross between
campaigns (CLAUDE.md, isolation), and the publisher's side of the database holds no sender
either (see `PACK_SUGGESTIONS` in the schema). Nothing changes until a DM of the publishing
campaign presses Add to pack, which puts the entry in that campaign's own homebrew and makes the
next version of the pack (paid plan, a DM, nothing from a book). The sender hears the outcome in
one line at its next session start. Buttons survive a restart
(`bot.add_dynamic_items(PackSuggestionButton)`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.logs import set_log_context
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_suggestions import PackSent, PackSuggestion, PackSuggestionStore
from dmbot.ui.dmbot_commands import _answer_first, _bot, _tell

log = logging.getLogger(__name__)

ONLY_DMS = "Only this campaign's DMs can answer this."
GONE = "That campaign isn't here any more."
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
ANSWERED = "Someone already answered this suggestion."
EXPIRED = "That suggestion is too old to add. Its sender can send it again."
SHOWN_MAX = 120  # an entry quoted in a one-line message
TEXT_MAX = MESSAGE_MAX - 200  # margin kept from before: room for the words a sender adds around it

_LABELS = {
    "see": ("See", discord.ButtonStyle.secondary),
    "accept": ("Add to pack", discord.ButtonStyle.primary),
    "no": ("No thanks", discord.ButtonStyle.secondary),
}


def _md(text: str) -> str:
    return str(discord.utils.escape_markdown(text))


def _first_line(entry: PackSuggestion, limit: int = SHOWN_MAX) -> str:
    shown = " ".join((entry.summary or entry.text).split())
    return shown if len(shown) <= limit else shown[: limit - 1] + "…"


def note_text(pack_name: str, entry: PackSuggestion, replaces: bool = False) -> str:
    """The publisher's note: the entry and the pack, nothing about the sender."""
    what = "It would replace your entry of that name." if replaces else "It would be a new entry."
    return (
        f"💡 A table using **{_md(pack_name)} homebrew** suggests (sender not shown):\n"
        f"**{_md(entry.name)}** ({entry.kind.capitalize()}): {_md(_first_line(entry))}\n"
        f"{what} Add to pack puts it in your homebrew and in the pack for every table that uses it,"
        " with any other changes you haven't published yet."
    )


def note_view(campaign_id: str, suggestion_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in _LABELS:
        view.add_item(PackSuggestionButton(campaign_id, action, suggestion_id))
    return view


class PackSuggestionButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:psugg:(?P<action>see|accept|no):(?P<campaign>[0-9a-f]{32}):(?P<sid>[0-9a-f]{32})",
):
    def __init__(self, campaign_id: str, action: str, suggestion_id: str) -> None:
        label, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:psugg:{action}:{campaign_id}:{suggestion_id}",
            )
        )
        self.campaign_id, self.action, self.suggestion_id = campaign_id, action, suggestion_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PackSuggestionButton:
        return cls(match["campaign"], match["action"], match["sid"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.action, self.suggestion_id)
        except Exception:
            log.exception("A pack suggestion button failed")
            await _tell(interaction, COULDNT)


def _store(bot: Any) -> PackSuggestionStore | None:
    sets = getattr(bot, "rule_sets", None)
    return sets if isinstance(sets, PackSuggestionStore) else None


async def _close_note(interaction: discord.Interaction, text: str) -> None:
    """Take the buttons off the note and say how it ended, so it can't be pressed twice."""
    message = interaction.message
    if message is None:
        return
    try:
        await message.edit(content=text, view=None)
    except discord.HTTPException:
        log.warning("Couldn't close a pack suggestion note", exc_info=True)


async def press(
    interaction: discord.Interaction, campaign_id: str, action: str, suggestion_id: str
) -> None:
    await _answer_first(interaction)
    bot = _bot(interaction)
    sets = _store(bot)
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
    asked = await sets.pack_suggestion(campaign.guild_id, campaign.id, suggestion_id)
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
            done = await sets.decline_pack_suggestion(
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


async def _see(interaction: discord.Interaction, campaign: Campaign, asked: PackSuggestion) -> None:
    """The whole entry, and whether Add to pack would replace one of the campaign's own."""
    bot = _bot(interaction)
    mine = None
    brew = getattr(bot, "homebrew", None)
    if brew is not None:
        mine = await brew.find(campaign.guild_id, campaign.id, asked.name)
    head = (
        f"If you press Add to pack, your entry **{_md(mine.name)}** is replaced by:"
        if mine is not None
        else "If you press Add to pack, this new entry goes into your homebrew and the pack:"
    )
    text = f"{head}\n**{_md(asked.name)}** ({asked.kind.capitalize()}): {_md(asked.text)}"
    await _tell(interaction, fit_message(text, limit=TEXT_MAX))


async def _accept(
    interaction: discord.Interaction, campaign: Campaign, asked: PackSuggestion
) -> None:
    bot = _bot(interaction)
    sets = _store(bot)
    assert sets is not None  # checked by the caller
    refused = await bot.plan_gate("share", campaign.guild_id, campaign, interaction.user.id)
    if refused is not None:
        await _tell(interaction, refused)
        return
    try:
        name, version = await sets.accept_pack_suggestion(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            asked.id,
            plan_ok=True,  # checked just above with plan_gate
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    changed = getattr(bot, "homebrew_changed", None)
    if changed is not None:  # a running session reads the campaign's homebrew names again
        changed(campaign.guild_id, campaign.id)
    done = f"✅ Added to **{_md(name[:60])}**: it is now version {version}."
    await _close_note(
        interaction, f"{interaction.message.content if interaction.message else ''}\n{done}"
    )
    await _tell(interaction, "Tables using it get it at their next session start.")


async def post_note(
    bot: Any, guild_id: int, campaign_id: str, suggestion: PackSuggestion, pack_name: str
) -> bool:
    """Put the note on the publishing campaign's DM screen. False if it could not be posted
    (it is tried again at that campaign's next session start)."""
    sets = _store(bot)
    if sets is None:
        return False
    try:
        campaign = await bot.campaigns.get(guild_id, campaign_id)
        if campaign is None or campaign.dm_screen_channel_id is None:
            return False
        replaces = False
        brew = getattr(bot, "homebrew", None)
        if brew is not None:
            replaces = await brew.find(guild_id, campaign_id, suggestion.name) is not None
        posted = await bot.post_message(
            campaign.dm_screen_channel_id,
            note_text(pack_name, suggestion, replaces),
            note_view(campaign_id, suggestion.id),
        )
        if posted is None:
            return False
        await sets.mark_pack_noted(guild_id, campaign_id, suggestion.id)
        return True
    except Exception:
        log.exception("Couldn't post a pack suggestion note")
        return False


def send_note(
    bot: Any, guild_id: int, campaign_id: str, suggestion_id: str, pack_name: str
) -> None:
    """Post the note for a suggestion just sent, in a task of its own. The sender's button press
    tags its logs with the SENDER's server; the note is the publisher's business, so the task
    re-tags its logs with the publisher's server and campaign, and a failed post can never put
    the two in one log line."""

    async def apart() -> None:
        set_log_context(guild_id=guild_id, campaign_id=campaign_id)
        sets = _store(bot)
        if sets is None:
            return
        found = await sets.pack_suggestion(guild_id, campaign_id, suggestion_id)
        if found is not None:  # a failed post is tried again at the publisher's next session
            await post_note(bot, guild_id, campaign_id, found, pack_name)

    bot.track(apart(), "pack-suggestion-note")


async def post_waiting(bot: Any, campaign: Campaign) -> None:
    """At a session's start: notes that never reached the DM screen are posted now."""
    sets = _store(bot)
    if sets is None:
        return
    try:
        for suggestion, pack_name in await sets.unnoted_pack_suggestions(
            campaign.guild_id, campaign.id
        ):
            if not await post_note(bot, campaign.guild_id, campaign.id, suggestion, pack_name):
                return  # no screen, or Discord is down: the rest would fail the same way
    except Exception:
        log.exception("Couldn't post waiting pack suggestion notes")


def answer_line(sent: PackSent) -> str:
    pack = _md(sent.set_name)
    name = _md(sent.name)
    if sent.status == "accepted":
        return f"💡 The **{pack}** DM added your suggestion (now version {sent.version}): {name}"
    if sent.status == "present":
        return f"💡 The **{pack}** DM already had that entry, so nothing was added: {name}"
    if sent.status == "gone":
        return f"💡 **{pack}** isn't being shared any more, so your suggestion wasn't used: {name}"
    if sent.status == "expired":
        return f"💡 No answer yet from the **{pack}** DM. You can send it again: {name}"
    return f"💡 The **{pack}** DM said no thanks to your suggestion: {name}"


async def tell_answers(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """At a session's start: one line for each suggestion this campaign sent that has an answer.
    Never raises. Each is told once."""
    sets = _store(bot)
    if sets is None:
        return
    try:
        answered = [
            s
            for s in await sets.pack_sent_answers(campaign.guild_id, campaign.id)
            if s.status != "open"
        ]
        if not answered:
            return
        if await bot.post(screen_id, "\n".join(answer_line(s) for s in answered)):
            await sets.mark_pack_told(campaign.guild_id, campaign.id, [s.id for s in answered])
    except Exception:
        log.exception("Couldn't tell the answers to pack suggestions")
