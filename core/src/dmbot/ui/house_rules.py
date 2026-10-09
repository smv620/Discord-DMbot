"""`/dmbot houserules`: a campaign's house rules, in a private reply (#865).

Anyone in the server may read them. Only the campaign's DMs get the buttons: **Add**,
and **Edit** and **Remove** for each rule (Remove asks first). With up to four rules, each
has its own two buttons; with more, a menu picks the rule. A long list is shown in pages.
Every change goes through `HouseRuleStore`, which checks again that the person is a DM.
DMbot never writes or decides a house rule: it only keeps what the DM typed.
"""

from __future__ import annotations

import logging
from functools import partial

import discord

from dmbot.campaigns import Campaign
from dmbot.logs import set_log_context
from dmbot.rules import house
from dmbot.rules.house import RULE_MAX, HouseRule, HouseRuleError, HouseRuleStore
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
    _Button,
    _failed,
    _Menu,
    _now,
    _replace,
    _Select,
    _send,
    _tell,
    dmbot_group,
)

log = logging.getLogger(__name__)

PAGE_RULES = 10  # most rules on one page
# Discord allows 2,000 characters. The list gets TEXT_MAX; around it go the note (cut to
# NOTE_MAX), the heading (the campaign's name cut to NAME_MAX), the intro and the page line.
TEXT_MAX = 1300
NOTE_MAX = 160
NAME_MAX = 100
ENTRY_MAX = 1000  # one rule, after escaping (a rule and its "instead of" are 500 each)
BUTTON_RULES = 4  # up to this many rules in all: Edit and Remove buttons for each

NOT_READY = "House rules aren't available right now. Please try again in a moment."
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
GONE = "That campaign isn't here any more. Use `/dmbot houserules` to see the list again."
STALE = "The list changed, so here is the new one. Pick again."
ALREADY_GONE = "That house rule was already removed. Here is the list now."
SOMEONE_REMOVED = (
    "Someone removed that house rule while you were editing, so your change wasn't saved."
)
MORE_CAMPAIGNS = "_Showing the 25 played most recently._"
NONE_YET = "No house rules yet."
ADD_HINT = "Press **Add a house rule** to write the first one."
DM_INTRO = (
    "The rules your table decided on. When one disagrees with the official rules, your "
    "table's rule is used. Everyone in the server can read this list; only a DM of this "
    "campaign can change it. You decide: DMbot never makes up a house rule."
)
READ_INTRO = (
    "The rules this table decided on. When one disagrees with the official rules, the "
    "table's rule is used. Everyone in the server can read this list; only a DM of this "
    "campaign can change it. DMbot never makes up a house rule."
)

ADD_LABEL = "➕ Add a house rule"
NEWER_LABEL = "◀ Newer"
OLDER_LABEL = "Older ▶"
BACK_LABEL = "Back to the list"
EDIT_LABEL = "Edit"
REMOVE_LABEL = "Remove"
YES_REMOVE_LABEL = "Yes, remove it"
KEEP_LABEL = "Keep it"
PICK_PLACEHOLDER = "Pick a rule to change…"


def _md(text: str) -> str:
    return discord.utils.escape_markdown(text)


def _fit(text: str, limit: int) -> str:
    """Shorten text to `limit` characters, never ending on half an escape."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if (len(cut) - len(cut.rstrip("\\"))) % 2:  # a lone backslash would escape the "…"
        cut = cut[:-1]
    return cut + "…"


def entry_words(rule: HouseRule) -> str:
    """A rule without its number: `Crits double the dice (instead of: …)`."""
    text = _md(rule.rule)
    if rule.supersedes:
        text += f" (instead of: {_md(rule.supersedes)})"
    return text


def entry_text(number: int, rule: HouseRule) -> str:
    """One rule as a numbered line: `3. Crits double the dice (instead of: …)`."""
    return _fit(f"{number}. {entry_words(rule)}", ENTRY_MAX)


def short_words(rule: HouseRule) -> str:
    """A rule's words for a note about it: cut short, never half an escape."""
    return _fit(_md(rule.rule), 100)


def pages(rules: list[HouseRule]) -> list[list[int]]:
    """Which rules (their places in the list) go on each page: as many as fit in one
    message, at most `PAGE_RULES`. Always at least one page."""
    out: list[list[int]] = [[]]
    used = 0
    for place, rule in enumerate(rules):
        size = len(entry_text(place + 1, rule)) + 1
        if out[-1] and (len(out[-1]) >= PAGE_RULES or used + size > TEXT_MAX):
            out.append([])
            used = 0
        out[-1].append(place)
        used += size
    return out


def list_text(
    campaign: Campaign, rules: list[HouseRule], page: int, *, is_dm: bool, note: str = ""
) -> str:
    """The message: what just happened (first, where a phone shows it), the heading, and
    this page of the numbered list, newest first."""
    lines = [_fit(note, NOTE_MAX)] if note else []
    lines.append(f"📜 **House rules: {_fit(_md(campaign.name), NAME_MAX)}**")
    lines.append(DM_INTRO if is_dm else READ_INTRO)
    shown = pages(rules)
    if not rules:
        lines.append(NONE_YET + (" " + ADD_HINT if is_dm else ""))
    for place in shown[page]:
        lines.append(entry_text(place + 1, rules[place]))
    if len(shown) > 1:
        lines.append(f"_Page {page + 1} of {len(shown)}. Newest first._")
    return "\n".join(lines)


def _store(interaction: discord.Interaction) -> HouseRuleStore | None:
    return _bot(interaction).house_rules


async def show_list(
    interaction: discord.Interaction,
    campaign: Campaign,
    *,
    page: int = 0,
    note: str = "",
    first: bool = False,
) -> None:
    """Draw the list: as a new private message (`first`), or in place of this one."""
    store = _store(interaction)
    if store is None:
        await _tell(interaction, NOT_READY)
        return
    rules = await store.list(campaign.guild_id, campaign.id)
    is_dm = interaction.user.id in campaign.dm_user_ids
    page = min(max(page, 0), len(pages(rules)) - 1)
    text = list_text(campaign, rules, page, is_dm=is_dm, note=note)
    menu = ListMenu(campaign, rules, page, is_dm=is_dm)
    view = menu if menu.children else None
    if first:
        await _send(interaction, text, view)
    else:
        await _show(interaction, text, view)


async def _show(interaction: discord.Interaction, text: str, view: _Menu | None) -> None:
    """Change the message this button, menu or form came from; if that can't be done
    (the message is gone), send the same as a new private message."""
    try:
        await _replace(interaction, text, view)
    except discord.HTTPException:
        log.warning("Couldn't change a house rules message; sent a new one", exc_info=True)
        await _send(interaction, text, view)


async def _redraw(
    interaction: discord.Interaction, campaign: Campaign, *, note: str = "", page: int = 0
) -> None:
    """Answer Discord, then draw the list again from the database (after a change, or a
    page turn), telling what happened."""
    await _answer_first(interaction, in_place=True)
    current = await _bot(interaction).campaigns.get(campaign.guild_id, campaign.id)
    if current is None:
        await _tell(interaction, GONE)
        return
    await show_list(interaction, current, page=page, note=note)


class ListMenu(_Menu):
    """The list's buttons: Add and page turning, and for a DM Edit and Remove."""

    def __init__(
        self, campaign: Campaign, rules: list[HouseRule], page: int, *, is_dm: bool
    ) -> None:
        super().__init__()
        self.campaign, self.rules, self.page = campaign, rules, page
        shown = pages(rules)
        if is_dm:
            self.add_item(_Button(self._add, label=ADD_LABEL, style=discord.ButtonStyle.primary))
        if page > 0:
            self.add_item(_Button(self._newer, label=NEWER_LABEL))
        if page < len(shown) - 1:
            self.add_item(_Button(self._older, label=OLDER_LABEL))
        if not is_dm or not rules:
            return
        if len(rules) <= BUTTON_RULES:
            for place, rule in enumerate(rules):
                self.add_item(
                    _Button(
                        partial(open_edit, campaign, rule, place),
                        label=f"{EDIT_LABEL} {place + 1}",
                        row=place + 1,
                    )
                )
                self.add_item(
                    _Button(
                        partial(ask_remove, campaign, rule, place),
                        label=f"{REMOVE_LABEL} {place + 1}",
                        style=discord.ButtonStyle.danger,
                        row=place + 1,
                    )
                )
        else:
            options = [
                discord.SelectOption(
                    label=logic.shorten(f"{place + 1}. {rules[place].rule}", logic.PHONE_LABEL_MAX),
                    value=str(rules[place].id),
                )
                for place in shown[page]
            ]
            self.pick = _Select(self._picked, placeholder=PICK_PLACEHOLDER, options=options)
            self.add_item(self.pick)

    async def _add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddForm(self.campaign))

    async def _newer(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page - 1)

    async def _older(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page + 1)

    async def _picked(self, interaction: discord.Interaction) -> None:
        wanted = int(self.pick.values[0])
        place = next((i for i, r in enumerate(self.rules) if r.id == wanted), None)
        if place is None:  # not on the list any more
            await _redraw(interaction, self.campaign, note=STALE)
            return
        rule = self.rules[place]
        text = f"📜 **House rule {place + 1}**\n{entry_text(place + 1, rule)}"
        await _show(interaction, text, RuleMenu(self.campaign, rule, place))


class RuleMenu(_Menu):
    """One rule picked from the menu: Edit, Remove, or back to the list."""

    def __init__(self, campaign: Campaign, rule: HouseRule, place: int) -> None:
        super().__init__()
        self.campaign = campaign
        self.add_item(_Button(partial(open_edit, campaign, rule, place), label=EDIT_LABEL))
        self.add_item(
            _Button(
                partial(ask_remove, campaign, rule, place),
                label=REMOVE_LABEL,
                style=discord.ButtonStyle.danger,
            )
        )
        self.add_item(_Button(self._back, label=BACK_LABEL))

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign)


async def open_edit(
    campaign: Campaign, rule: HouseRule, place: int, interaction: discord.Interaction
) -> None:
    await interaction.response.send_modal(EditForm(campaign, rule, place))


async def ask_remove(
    campaign: Campaign, rule: HouseRule, place: int, interaction: discord.Interaction
) -> None:
    """Never remove at once: say which rule, and that it can't be undone."""
    text = f"Remove this house rule?\n{entry_text(place + 1, rule)}\nThis can't be undone."
    await _show(interaction, text, ConfirmRemove(campaign, rule, place))


class ConfirmRemove(_Menu):
    def __init__(self, campaign: Campaign, rule: HouseRule, place: int) -> None:
        super().__init__()
        self.campaign, self.rule, self.place = campaign, rule, place
        self.add_item(_Button(self._yes, label=YES_REMOVE_LABEL, style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label=KEEP_LABEL))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        c = self.campaign
        try:
            gone = await store.remove(
                c.guild_id,
                c.id,
                interaction.user.id,
                self.rule.id,
                unchanged_since=self.rule.updated_at,
            )
        except HouseRuleError as exc:
            note = ALREADY_GONE if str(exc) == house.GONE else str(exc)
        else:
            note = f"🗑 Removed house rule: {short_words(gone)}"
        await _redraw(interaction, c, note=note)

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign)


class _RuleForm(discord.ui.Modal):
    """The two boxes shared by Add and Edit."""

    rule: discord.ui.TextInput[_RuleForm] = discord.ui.TextInput(
        label="The rule",
        placeholder="For example: A natural 20 doubles the damage dice",
        style=discord.TextStyle.paragraph,
        max_length=RULE_MAX,
    )
    instead: discord.ui.TextInput[_RuleForm] = discord.ui.TextInput(
        label="Which rule does it change? (optional)",
        placeholder="Leave empty if this is a new rule, not a change",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=RULE_MAX,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        set_log_context(guild_id=interaction.guild_id)  # tag this form's logs
        return True

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        try:
            note = await self.save(store, interaction.user.id)
        except HouseRuleError as exc:
            # The form is closed: give the words back, so they can be pasted again.
            words = " ".join(self.rule.value.split())
            said = f"\nYour words, to copy: {_fit(_md(words), 300)}" if words else ""
            await _tell(interaction, self.refusal(str(exc)) + said)
            return
        await _redraw(interaction, self.campaign, note=note)

    def refusal(self, message: str) -> str:
        return message

    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        raise NotImplementedError


class AddForm(_RuleForm, title="Add a house rule"):
    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        c = self.campaign
        saved = await store.add(c.guild_id, c.id, user_id, self.rule.value, self.instead.value)
        return f"➕ Added house rule: {short_words(saved)}"


class EditForm(_RuleForm, title="Edit a house rule"):
    def __init__(self, campaign: Campaign, rule: HouseRule, place: int) -> None:
        super().__init__(campaign)
        self.existing, self.place = rule, place
        self.rule.default = rule.rule
        self.instead.default = rule.supersedes

    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        c = self.campaign
        changed = await store.edit(
            c.guild_id, c.id, user_id, self.existing.id, self.rule.value, self.instead.value
        )
        return f"✏️ Changed house rule: {short_words(changed)}"

    def refusal(self, message: str) -> str:
        return SOMEONE_REMOVED if message == house.GONE else message


class CampaignChoice(_Menu):
    """Which campaign's house rules? (More than one, and none is being played.)"""

    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        self.campaigns = {c.id: c for c in campaigns}
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
            await _tell(interaction, GONE)
            return
        self.stop()
        await _redraw(interaction, campaign)


@dmbot_group.command(
    name="houserules",
    description="See this table's house rules (a DM can add, edit or remove them)",
)
async def dmbot_house_rules(interaction: discord.Interaction) -> None:
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
    playing = bot.active_campaign_id(guild.id)
    mine = [c for c in everyone if interaction.user.id in c.dm_user_ids]
    current = next((c for c in everyone if c.id == playing), None)
    if current is None and len(mine) == 1:
        current = mine[0]
    if current is None and len(everyone) == 1:
        current = everyone[0]
    if current is not None:
        await show_list(interaction, current, first=True)
        return
    # More than one and none playing: everyone may read any campaign's, so offer them all.
    ask = "**Which campaign's house rules?**"
    if len(everyone) > logic.SELECT_OPTIONS_MAX:
        ask += "\n" + MORE_CAMPAIGNS
    await _send(interaction, ask, CampaignChoice(everyone))
