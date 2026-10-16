"""Looking up a rule (#908): the DM asks for a spell, a condition or a creature by name, and
gets what the free rules (the SRD) say, privately, with where it comes from.

Two ways in, both for a campaign's DMs only (for now): the 📖 **Look up a rule** button on the
DM screen's ⚙️ Settings card, which opens a form with one box, and `/dmbot rule name:` with a
list of names that opens as they type. The answer is a private message: never in a channel,
never in a transcript. A house rule that names the thing comes first; the book comes after;
the DM decides what applies. Nothing is guessed: no match means "couldn't find that", with
close names to press.

The card itself is built in `dmbot.ui.rule_card`, which has no Discord in it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.campaigns.models import DEFAULT_FALLBACK, DEFAULT_TARGET
from dmbot.logs import set_log_context
from dmbot.rules import index
from dmbot.rules.homebrew import Homebrew
from dmbot.rules.house import HouseRule
from dmbot.ui import logic, rule_card
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
    _Button,
    _failed,
    _Menu,
    _now,
    _Select,
    _send,
    _tell,
    dmbot_group,
)

log = logging.getLogger(__name__)

FORM_TITLE = "Look up a rule"
BOX_LABEL = "Spell, condition or creature"
BOX_PLACEHOLDER = "For example: Fireball, Grappled or Goblin"
BOX_MAX = 100
READ_REST_LABEL = "Read the rest"
MAKE_MINE_LABEL = "✏️ Make it my own"
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
ONLY_DMS = "Only this campaign's DM can look up rules for now. Ask your DM."
ONLY_DMS_ANY = "Only a campaign's DM can look up rules for now. Ask your DM."
COULDNT_COPY = "Couldn't copy that. Nothing was changed. Try again in a moment."
NO_HOMEBREW = "Homebrew isn't available right now. Ask whoever runs DMbot."
ALREADY_MINE = "You already made this one your own. Find it in `/dmbot homebrew` to change it."
FULL_COPY = (
    "This campaign's homebrew is full. Remove one in `/dmbot homebrew`, then press this again."
)
CAMPAIGN_GONE = "That campaign isn't here any more. Use `/dmbot rule` again."
WHICH_CAMPAIGN = "**Which campaign is this for?** Pick one and DMbot looks it up."
EMPTY = "Type a spell, a condition or a creature, then try again."
TOO_LONG = f"That's too long for a name. Use at most {BOX_MAX} characters."
NAMES_MORE = 25  # most choices Discord shows as a person types
TYPEAHEAD_WAIT_S = 1.0  # the longest the list waits for the database
SUGGESTION_MAX = 80  # a button's label may hold 80 characters


def may_look_up(campaign: Campaign, user_id: int) -> bool:
    """Only a campaign's DMs, for now (decided 2026-10-09)."""
    return user_id in campaign.dm_user_ids


async def _house_rules(interaction: discord.Interaction, campaign: Campaign) -> list[HouseRule]:
    """This campaign's house rules and no one else's (the store reads one campaign's at a
    time); none if the store isn't there or can't be read, so the card still shows."""
    try:
        rules: list[HouseRule] = await _bot(interaction).all_house_rules(
            campaign.guild_id, campaign.id
        )
    except Exception:
        log.exception("Couldn't read the house rules for a rule lookup")
        return []
    return rules


async def _homebrew(
    interaction: discord.Interaction, campaign: Campaign, typed: str
) -> Homebrew | None:
    """This campaign's own homebrew entry with exactly this name (any case), none if there isn't
    one or it can't be read, so the free rules' card still shows."""
    store = _bot(interaction).homebrew_reader
    if store is None:
        return None
    try:
        return await store.find(campaign.guild_id, campaign.id, typed)
    except Exception:
        log.exception("Couldn't read the homebrew for a rule lookup")
        return None


async def look_up(
    interaction: discord.Interaction,
    campaign_id: str,
    typed: str,
    *,
    guild_id: int,
    kind: str | None = None,
) -> None:
    """Find `typed` for this campaign and send the card privately. The campaign is read
    again and the person checked again: a button or a form can be used minutes later."""
    await _answer_first(interaction)
    typed = " ".join(typed.split())
    if not typed:
        await _tell(interaction, EMPTY)
        return
    if len(typed) > BOX_MAX:
        await _tell(interaction, TOO_LONG)
        return
    campaign = await _bot(interaction).campaigns.get(guild_id, campaign_id)
    if campaign is None:
        await _tell(interaction, CAMPAIGN_GONE)
        return
    if not may_look_up(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return
    rules = await _house_rules(interaction, campaign)
    srd = index.srd()
    hit = srd.lookup(typed, campaign.target_ruleset, campaign.fallback_ruleset, kind=kind)
    # The campaign's own homebrew sits between its house rules and the book (PLAN, rules
    # precedence), and a homebrew name shadows the book's, hidden or not: the book's card for the
    # same name is not shown. This is the DM's private answer, so an entry players may not know
    # is shown too.
    own = await _homebrew(interaction, campaign, typed)
    if own is not None:
        mine = rule_card.homebrew_parts(own, campaign.name)
        # A followed pack's entry can be copied into the campaign's own homebrew (#1541).
        copy = own if own.source else None
        await _send(interaction, mine[0], Card(mine, 1, copy) if len(mine) > 1 or copy else None)
        return
    if hit is None:
        close = srd.suggest(typed, campaign.target_ruleset, campaign.fallback_ruleset)
        text = rule_card.no_match_text(typed, rules, bool(close))
        await _send(interaction, text, Suggestions(campaign, close) if close else None)
        return
    parts = rule_card.card_parts(hit, typed, rules)
    await _send(interaction, parts[0], Card(parts, 1) if len(parts) > 1 else None)


class _KeepsItsText(_Menu):
    """A menu under a card or a list of names: when it times out only the buttons go; the
    words stay, because the DM may still be reading them."""

    async def on_timeout(self) -> None:
        if self.origin is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.origin.edit_original_response(view=None)


class Card(_KeepsItsText):
    """The card's later parts: a **Read the rest** button sends the next one, privately."""

    def __init__(
        self, parts: list[str], next_part: int, pack_entry: Homebrew | None = None
    ) -> None:
        super().__init__()
        self.parts, self.next_part, self.pack_entry = parts, next_part, pack_entry
        if next_part < len(parts):
            label = f"{READ_REST_LABEL} ({next_part + 1} of {len(parts)})"
            self.add_item(_Button(self._more, label=label, style=discord.ButtonStyle.primary))
        if pack_entry is not None:
            self.add_item(_Button(partial(make_mine, pack_entry), label=MAKE_MINE_LABEL))

    async def _more(self, interaction: discord.Interaction) -> None:
        # The pressed message loses its button (so a part is never sent twice), then the
        # next part comes as a new private message with its own button.
        # The Make-it-my-own button stays on the first message (it is the entry's, not a part's).
        keep = Card(self.parts, len(self.parts), self.pack_entry) if self.pack_entry else None
        await interaction.response.edit_message(view=keep)
        follow = self.next_part + 1
        view = Card(self.parts, follow) if follow < len(self.parts) else None
        await _send(interaction, self.parts[self.next_part], view)


async def make_mine(entry: Homebrew, interaction: discord.Interaction) -> None:
    """✏️ Make it my own on a followed pack's entry: copy it into the campaign's own homebrew as it
    is, then open Edit on the copy. The campaign's own entry of that name wins over the pack's from
    now on (PLAN, rules precedence). The person is checked again here: a card can be pressed
    minutes later, and the copy is a change to the campaign. A pack holds no book text (#1529), so
    the copy is never marked from_book."""
    from dmbot.rules.homebrew import FULL, SAME_NAME, HomebrewError
    from dmbot.ui.homebrew import MakeMineForm

    bot = _bot(interaction)
    campaign = (
        await bot.campaigns.get(interaction.guild.id, entry.campaign_id)
        if interaction.guild is not None
        else None
    )
    if campaign is None:
        await _tell(interaction, CAMPAIGN_GONE)
        return
    if not may_look_up(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return
    store = bot.homebrew
    if store is None:
        await _tell(interaction, NO_HOMEBREW)
        return
    try:
        saved = await store.add(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            kind=entry.kind,
            name=entry.name,
            text=entry.text,
            summary=entry.summary,
            players_may_know=entry.players_may_know,
        )
    except Exception as exc:
        if not isinstance(exc, HomebrewError):
            log.exception("Couldn't copy a pack entry into the campaign's homebrew")
            await _tell(interaction, COULDNT_COPY)
            return
        # Full, or an own entry of that name already exists (a second press, or another device).
        # Each case gets its own sentence; the store's words would say to pick another name.
        text = (
            ALREADY_MINE if str(exc) == SAME_NAME else FULL_COPY if str(exc) == FULL else str(exc)
        )
        await _tell(interaction, text)
        return
    notify = getattr(bot, "homebrew_changed", None)
    if notify is not None:
        notify(campaign.guild_id, campaign.id)
    await interaction.response.send_modal(MakeMineForm(campaign, saved, 0))
    # The copy exists now, so the button has done its job: take it off this card, so Cancel on
    # the form leaves a plain card. If Discord won't let the card change, a second press says
    # ALREADY_MINE.
    with contextlib.suppress(discord.HTTPException):
        await interaction.edit_original_response(view=None)


class Suggestions(_KeepsItsText):
    """ "Did you mean…?": close names as buttons. Pressing one reads it; nothing is picked
    for the DM."""

    def __init__(self, campaign: Campaign, close: list[index.Entry]) -> None:
        super().__init__()
        shown: dict[tuple[str, str], index.Entry] = {}
        for entry in close:  # one button for each name and kind (a spell and a creature can share)
            shown.setdefault((entry.name, entry.kind), entry)
        names = [name for name, _ in shown]
        for (name, kind), entry in list(shown.items())[: logic.SELECT_OPTIONS_MAX]:
            tag = f" [Legacy {entry.edition}]" if entry.edition == index.LEGACY else ""
            if names.count(name) > 1:  # say which, when the same name is two things
                tag = f" ({rule_card.KIND_WORDS.get(kind, kind)}){tag}"
            label = logic.shorten(name, SUGGESTION_MAX - len(tag)) + tag
            self.add_item(_Button(partial(look_up_name, campaign, name, kind), label=label))


async def look_up_name(
    campaign: Campaign, name: str, kind: str, interaction: discord.Interaction
) -> None:
    """A suggested name was pressed: read that name, of that kind."""
    await look_up(interaction, campaign.id, name, guild_id=campaign.guild_id, kind=kind)


class LookupForm(discord.ui.Modal, title=FORM_TITLE):
    """One box. The campaign is the one whose Settings card it was opened from."""

    name: discord.ui.TextInput[LookupForm] = discord.ui.TextInput(
        label=BOX_LABEL,
        placeholder=BOX_PLACEHOLDER,
        max_length=BOX_MAX,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)
        return True

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await look_up(
            interaction, self.campaign.id, self.name.value, guild_id=self.campaign.guild_id
        )


class CampaignChoice(_Menu):
    """Which campaign? (The person is a DM of more than one, and none is being played.)"""

    def __init__(self, campaigns: list[Campaign], typed: str) -> None:
        super().__init__()
        self.campaigns = {c.id: c for c in campaigns}
        self.typed = typed
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
        campaign = self.campaigns.get(self.pick.values[0])
        if campaign is None:
            await _tell(interaction, CAMPAIGN_GONE)
            return
        self.stop()
        await look_up(interaction, campaign.id, self.typed, guild_id=campaign.guild_id)


async def _typeahead(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Names that begin as the DM types, newest rules first (at most 25)."""
    target, fallback = await _rulesets(interaction)
    found = index.srd().typeahead(current, target, fallback, limit=NAMES_MORE)
    return [
        app_commands.Choice(name=rule_card.choice_label(e), value=e.name[:BOX_MAX]) for e in found
    ]


async def _rulesets(interaction: discord.Interaction) -> tuple[str, str]:
    """The ruleset order for the list of names: the playing campaign's, if the person is its
    DM. Discord waits 3 seconds for the list, so the database gets one: slower, or broken,
    and the defaults are used (the names are the same, only the order differs)."""
    guild = interaction.guild
    bot = _bot(interaction)
    if guild is None:
        return DEFAULT_TARGET, DEFAULT_FALLBACK
    try:
        playing = bot.active_campaign_id(guild.id)
        campaign = (
            await asyncio.wait_for(bot.campaigns.get(guild.id, playing), TYPEAHEAD_WAIT_S)
            if playing
            else None
        )
    except Exception:
        log.warning(
            "Couldn't read the campaign for the rule list; used the defaults", exc_info=True
        )
        return DEFAULT_TARGET, DEFAULT_FALLBACK
    if campaign is not None and may_look_up(campaign, interaction.user.id):
        return campaign.target_ruleset, campaign.fallback_ruleset
    return DEFAULT_TARGET, DEFAULT_FALLBACK


@dmbot_group.command(
    name="rule",
    description="Look up a spell, condition or creature in the free rules (the DM only)",
)
@app_commands.describe(name="A spell, a condition or a creature: start typing and pick one")
@app_commands.autocomplete(name=_typeahead)
async def dmbot_rule(interaction: discord.Interaction, name: str) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    await _answer_first(interaction)  # the database can be slow; Discord waits 3 seconds
    bot = _bot(interaction)
    everyone = await bot.campaigns.list_campaigns(guild.id)
    if not everyone:
        await _tell(interaction, NO_CAMPAIGNS)
        return
    mine = [c for c in everyone if may_look_up(c, interaction.user.id)]
    if not mine:
        await _tell(interaction, ONLY_DMS_ANY)
        return
    playing = bot.active_campaign_id(guild.id)
    current = next((c for c in mine if c.id == playing), None)
    if current is None and len(mine) == 1:
        current = mine[0]
    if current is not None:
        await look_up(interaction, current.id, name, guild_id=guild.id)
        return
    await _send(interaction, WHICH_CAMPAIGN, CampaignChoice(mine, name))
