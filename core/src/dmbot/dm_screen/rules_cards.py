"""Rules cards from the table (#931): when a spell, condition or creature is named in a live
session, a short card goes on the DM screen, if the campaign's DMs turned that on in
⚙️ Settings (off by default).

DMbot only shows what the free rules (the SRD) say; the DM decides what applies. A card
says what was heard, names its source, and shows a house rule that mentions the thing
first. Its buttons: **✅ Got it** and **🙈 Ignore** take the buttons away (Ignore: no more
cards for that name this session), **⚖️ Override** opens the "Add a house rule" form with
"Instead of" filled in, and **📖 Read it all** sends the full lookup card privately.

Not noisy: one card for each name each session, one card a minute whatever the names (the
rest are dropped, not kept for later), and nothing once the session is over.

Here: the limits (`RulesCards`, pure) and the buttons. The noticing is in
`dmbot.rules.spotter`; the posting is `DMBot._note_rules`. Register with
`bot.add_dynamic_items(RulesCardButton)`.
"""

from __future__ import annotations

import functools
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import discord

from dmbot.rules import index
from dmbot.rules.spotter import Mention, Spotter

log = logging.getLogger(__name__)

NO_PINGS = discord.AllowedMentions.none()
GAP_S = 60.0  # at most one card in this many seconds, whichever names are said
CLOSED = (
    "This card is from a finished session, so its buttons no longer work. To look something "
    "up, press 📖 Look up a rule in ⚙️ Settings."
)
ONLY_DMS = "Only this campaign's DMs can use these buttons. You can still read the card."
STOP_ALL = "To stop all cards: ⚙️ Settings, then Rules cards."
FAILED = "Something went wrong. Try again in a moment."
GOT_LABEL, IGNORE_LABEL, OVERRIDE_LABEL, READ_LABEL = "Got it", "Ignore", "Override", "Read it all"
ACTIONS = {"got": "✅", "ign": "🙈", "ovr": "⚖️", "all": "📖"}
LABELS = {"got": GOT_LABEL, "ign": IGNORE_LABEL, "ovr": OVERRIDE_LABEL, "all": READ_LABEL}


@dataclass(frozen=True, slots=True)
class Shown:
    """A card on the screen, for its buttons."""

    kind: str
    name: str  # the entry's name
    said: str  # as said


@dataclass(slots=True)
class RulesCards:
    """A session's cards: which names were shown or ignored, and when the last one was."""

    seen: set[tuple[str, str]] = field(default_factory=set)
    ignored: set[tuple[str, str]] = field(default_factory=set)
    last_at: float | None = None
    shown: dict[str, Shown] = field(default_factory=dict)

    def pick(self, mentions: Iterable[Mention], now: float) -> Mention | None:
        """The one name to show a card for, if any: not within a minute of the last card
        (the rest are dropped, never queued), and not a name shown or ignored already."""
        if self.last_at is not None and now - self.last_at < GAP_S:
            return None
        for mention in mentions:
            if mention.key not in self.seen and mention.key not in self.ignored:
                return mention
        return None

    def remember(self, mention: Mention, now: float) -> str:
        """The card is going up: count it, and give it an id for its buttons."""
        self.seen.add(mention.key)
        self.last_at = now
        card_id = uuid.uuid4().hex[:8]
        self.shown[card_id] = Shown(mention.entry.kind, mention.entry.name, mention.said)
        return card_id

    def forget(self, card_id: str, last_at: float | None) -> None:
        """The card could not be shown: the name and the minute are given back."""
        card = self.shown.pop(card_id, None)
        if card is not None:
            self.seen.discard((card.kind, index.normalize(card.name)))
            self.last_at = last_at

    def ignore(self, card_id: str) -> Shown | None:
        """No more cards for this card's name this session."""
        card = self.shown.get(card_id)
        if card is not None:
            self.ignored.add((card.kind, index.normalize(card.name)))
        return card


@functools.cache
def spotter_for(target: str, fallback: str) -> Spotter:
    """The names to look for in a campaign whose rulesets are these (kept: the index
    never changes)."""
    return Spotter.from_pool(index.srd().names_pool(target, fallback))


def card_view(guild_id: int, card_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ACTIONS:
        view.add_item(RulesCardButton(guild_id, card_id, action))
    return view


class RulesCardButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:rcard:(?P<action>got|ign|ovr|all):(?P<guild>[0-9]{1,20}):(?P<card>[0-9a-f]{8})",
):
    """One of a card's four buttons. The cards live with the running session, so after a
    restart, or once the session is over, a press just says the card is closed."""

    def __init__(self, guild_id: int, card_id: str, action: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=LABELS[action],
                emoji=ACTIONS[action],
                style=(
                    discord.ButtonStyle.primary
                    if action == "all"
                    else discord.ButtonStyle.secondary
                ),
                custom_id=f"dmbot:rcard:{action}:{guild_id}:{card_id}",
            )
        )
        self.guild_id, self.card_id, self.action = guild_id, card_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RulesCardButton:
        return cls(int(match["guild"]), match["card"], match["action"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.guild_id, self.card_id, self.action)
        except Exception:
            log.exception("A rules card button failed")
            if interaction.response.is_done():
                await interaction.followup.send(FAILED, ephemeral=True)
            else:
                await interaction.response.send_message(FAILED, ephemeral=True)


async def press(interaction: discord.Interaction, guild_id: int, card_id: str, action: str) -> None:
    """What a button does. The session and the person are checked again every time."""
    bot: Any = interaction.client
    table = bot.tables.get(guild_id) if interaction.guild_id == guild_id else None
    card = table.rules.shown.get(card_id) if table is not None else None
    if table is None or card is None or table.campaign_id is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    campaign = await bot.campaigns.get(guild_id, table.campaign_id)
    if campaign is None:
        await interaction.response.send_message(CLOSED, ephemeral=True)
        return
    if interaction.user.id not in campaign.dm_user_ids:
        await interaction.response.send_message(ONLY_DMS, ephemeral=True)
        return
    if action == "got":
        await interaction.response.edit_message(view=None)
    elif action == "ign":
        table.rules.ignore(card_id)
        message = interaction.message
        words = message.content if message is not None else ""
        name = discord.utils.escape_markdown(card.name)
        note = f"\n🙈 No more cards for {name} this session. {STOP_ALL}"
        await interaction.response.edit_message(content=words + note, view=None)
    elif action == "ovr":
        from dmbot.ui.house_rules import OverrideForm

        await interaction.response.send_modal(OverrideForm(campaign, card.name))
    else:  # "all"
        from dmbot.ui.rule_lookup import look_up

        await look_up(interaction, campaign.id, card.name, guild_id=guild_id, kind=card.kind)
