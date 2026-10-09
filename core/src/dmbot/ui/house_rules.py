"""`/dmbot houserules`: a campaign's house rules, in a private reply (#865).

Anyone in the server may read them. Only the campaign's DMs get the buttons: **Add**,
and **Edit** and **Remove** for the rules on the page being shown (Remove asks first).
When the page shows four rules or fewer, each has its own two buttons; with more, a menu
picks the rule. A long list is shown in pages, and the DM stays on their page after a
change. Each rule keeps its own number for good (see `dmbot.rules.house`): the list
shows it, and the buttons and the menu use it.

Every change goes through `HouseRuleStore`, which checks again that the person is a DM,
and (for Edit and Remove) that no other DM changed the rule since this one was shown.
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
BUTTON_RULES = 4  # a page with up to this many rules: Edit and Remove buttons for each

NOT_READY = "House rules aren't available right now. Please try again in a moment."
NO_CAMPAIGNS = "There's no campaign in this server yet. A DM can set one up with `/dmbot start`."
CAMPAIGN_GONE = "That campaign isn't here any more. Use `/dmbot houserules` to see the list again."
STALE = "The list changed, so here is the new one. Pick again."
ALREADY_GONE = "That house rule was already removed. Here is the list now."
CHANGED_REMOVE = "Another DM changed that house rule, so nothing was removed. Here is the list now."
MORE_CAMPAIGNS = "_Showing the 25 played most recently._"
NONE_YET_DM = "No house rules yet. Press **Add a house rule** to write the first one."
NONE_YET_PLAYER = "No house rules yet. Your DM can add them."
DM_INTRO = (
    "Your table's own rules; they beat the book. Everyone in the server can read them, and "
    "only this campaign's DMs can change them. DMbot never makes up a house rule: you decide."
)
READ_INTRO = (
    "This table's own rules; they beat the book. Only the DM can change them. DMbot never "
    "makes up a house rule."
)
CHANGED_MIND = "Changed your mind? Press **Add a house rule** and paste it."
NUMBERS_STAY = "_Each rule keeps its number, even when others are removed._"

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


def _code(text: str) -> str:
    """Words in a code block, exactly as typed, so they can be copied: nothing in them is
    read as formatting, and three backticks can't end the block early."""
    return "```\n" + text.strip().replace("```", "`​``") + "\n```"


def entry_words(rule: HouseRule) -> str:
    """A rule without its number: `Crits double the dice (instead of: …)`."""
    text = _md(rule.rule)
    if rule.supersedes:
        text += f" (instead of: {_md(rule.supersedes)})"
    return text


def entry_text(rule: HouseRule) -> str:
    """One rule as a line, with its own number: `12. Crits double the dice (instead of: …)`."""
    return _fit(f"{rule.number}. {entry_words(rule)}", ENTRY_MAX)


def pages(rules: list[HouseRule]) -> list[list[int]]:
    """Which rules (their places in the list) go on each page: as many as fit in one
    message, at most `PAGE_RULES`. Always at least one page."""
    out: list[list[int]] = [[]]
    used = 0
    for place, rule in enumerate(rules):
        size = len(entry_text(rule)) + 1
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
    this page of the list, newest first, each rule with its own number."""
    lines = [_fit(note, NOTE_MAX)] if note else []
    lines.append(f"📜 **House rules: {_fit(_md(campaign.name), NAME_MAX)}**")
    lines.append(DM_INTRO if is_dm else READ_INTRO)
    shown = pages(rules)
    if not rules:
        lines.append(NONE_YET_DM if is_dm else NONE_YET_PLAYER)
    if rules:
        lines.append(NUMBERS_STAY)
    for place in shown[page]:
        lines.append(entry_text(rules[place]))
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
    page turn), on `page`, telling what happened."""
    await _answer_first(interaction, in_place=True)
    current = await _bot(interaction).campaigns.get(campaign.guild_id, campaign.id)
    if current is None:
        await _tell(interaction, CAMPAIGN_GONE)
        return
    await show_list(interaction, current, page=page, note=note)


class ListMenu(_Menu):
    """The list's buttons: Add and page turning, and for a DM Edit and Remove for the
    rules on this page only."""

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
        on_page = [rules[place] for place in shown[page]]
        if len(on_page) <= BUTTON_RULES:
            for row, rule in enumerate(on_page, start=1):
                self.add_item(
                    _Button(
                        partial(open_edit, campaign, rule, page),
                        label=f"{EDIT_LABEL} {rule.number}",
                        row=row,
                    )
                )
                self.add_item(
                    _Button(
                        partial(ask_remove, campaign, rule, page),
                        label=f"{REMOVE_LABEL} {rule.number}",
                        style=discord.ButtonStyle.danger,
                        row=row,
                    )
                )
        else:
            options = [
                discord.SelectOption(
                    label=logic.shorten(f"{rule.number}. {rule.rule}", logic.PHONE_LABEL_MAX),
                    value=str(rule.number),
                    description=logic.shorten(rule.rule, logic.DESCRIPTION_MAX),
                )
                for rule in on_page
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
        rule = next((r for r in self.rules if r.number == wanted), None)
        if rule is None:  # not one of this page's (Discord only sends what the menu offered)
            await _redraw(interaction, self.campaign, note=STALE, page=self.page)
            return
        text = f"📜 **House rule {rule.number}**\n{entry_words(rule)}"
        await _show(interaction, text, RuleMenu(self.campaign, rule, self.page))


class RuleMenu(_Menu):
    """One rule picked from the menu: Edit, Remove, or back to the list (to the same page)."""

    def __init__(self, campaign: Campaign, rule: HouseRule, page: int) -> None:
        super().__init__()
        self.campaign, self.page = campaign, page
        # Labelled with the number, as in the list's own buttons ("Edit 12"), so the texts
        # that say "press Edit 12" are right wherever the DM is.
        self.add_item(
            _Button(partial(open_edit, campaign, rule, page), label=f"{EDIT_LABEL} {rule.number}")
        )
        self.add_item(
            _Button(
                partial(ask_remove, campaign, rule, page),
                label=f"{REMOVE_LABEL} {rule.number}",
                style=discord.ButtonStyle.danger,
            )
        )
        self.add_item(_Button(self._back, label=BACK_LABEL))

    async def _back(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


async def open_edit(
    campaign: Campaign, rule: HouseRule, page: int, interaction: discord.Interaction
) -> None:
    await interaction.response.send_modal(EditForm(campaign, rule, page))


async def ask_remove(
    campaign: Campaign, rule: HouseRule, page: int, interaction: discord.Interaction
) -> None:
    """Never remove at once: say which rule, and that it can't be undone."""
    text = (
        f"Remove house rule {rule.number}?\n{entry_text(rule)}\n"
        "You'll get the words back to copy if you change your mind. Its number won't be "
        "used again."
    )
    await _show(interaction, text, ConfirmRemove(campaign, rule, page))


class ConfirmRemove(_Menu):
    def __init__(self, campaign: Campaign, rule: HouseRule, page: int) -> None:
        super().__init__()
        self.campaign, self.rule, self.page = campaign, rule, page
        self.add_item(_Button(self._yes, label=YES_REMOVE_LABEL, style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label=KEEP_LABEL))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        store = _store(interaction)
        if store is None:
            await _tell(interaction, NOT_READY)
            return
        c = self.campaign
        gone: HouseRule | None = None
        try:
            gone = await store.remove(
                c.guild_id,
                c.id,
                interaction.user.id,
                self.rule.number,
                unchanged_since=self.rule.version,
            )
        except HouseRuleError as exc:
            note = {house.GONE: ALREADY_GONE, house.CHANGED: CHANGED_REMOVE}.get(str(exc), str(exc))
        else:
            note = f"🗑 Removed house rule {gone.number}."
        if gone is not None:  # the words in full, to copy back if it was a mistake: first,
            await _tell(interaction, removed_words(gone))  # so they're never lost
        await _redraw(interaction, c, note=note, page=self.page)

    async def _keep(self, interaction: discord.Interaction) -> None:
        await _redraw(interaction, self.campaign, page=self.page)


def removed_words(rule: HouseRule) -> str:
    """What was removed, whole and as typed, and how to get it back."""
    text = f"🗑 Removed house rule {rule.number}:\n{_code(rule.rule)}"
    if rule.supersedes:
        text += f"\nInstead of:\n{_code(rule.supersedes)}"
    return f"{text}\n{CHANGED_MIND}"


class _RuleForm(discord.ui.Modal):
    """The two boxes shared by Add and Edit."""

    rule: discord.ui.TextInput[_RuleForm] = discord.ui.TextInput(
        label="The rule",
        placeholder="For example: Drinking a potion is a bonus action",
        style=discord.TextStyle.paragraph,
        max_length=RULE_MAX,
    )
    instead: discord.ui.TextInput[_RuleForm] = discord.ui.TextInput(
        label="Instead of (optional)",
        placeholder="The book rule it replaces. Leave empty if it's new.",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=RULE_MAX,
    )

    def __init__(self, campaign: Campaign) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign = campaign
        self.page = 0  # where the list is drawn again after a save

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
            await _tell(interaction, self.refusal(str(exc)) + self.typed())
            if str(exc) in (house.CHANGED, house.GONE):
                # The list on screen is out of date (its buttons would use the old version
                # and be refused again): draw it as it is now.
                await _redraw(interaction, self.campaign, page=self.page)
            return
        await _redraw(interaction, self.campaign, note=note, page=self.page)

    def typed(self) -> str:
        """The form is closed by the time a refusal comes: give both boxes back, whole
        and exactly as typed, to copy into the form again."""
        text = ""
        if self.rule.value.strip():
            text += f"\nYour words, to copy:\n{_code(self.rule.value)}"
        if self.instead.value.strip():
            text += f"\nInstead of:\n{_code(self.instead.value)}"
        return text

    def refusal(self, message: str) -> str:
        return message

    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        raise NotImplementedError


class AddForm(_RuleForm, title="Add a house rule"):
    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        c = self.campaign
        saved = await store.add(c.guild_id, c.id, user_id, self.rule.value, self.instead.value)
        return f"➕ Added house rule {saved.number}."

    def refusal(self, message: str) -> str:
        if message == house.EMPTY:
            return f"{message} Press **{ADD_LABEL[2:]}** and type the rule."
        return message


class EditForm(_RuleForm, title="Edit a house rule"):
    def __init__(self, campaign: Campaign, rule: HouseRule, page: int) -> None:
        super().__init__(campaign)
        self.existing, self.page = rule, page
        self.rule.default = rule.rule
        self.instead.default = rule.supersedes

    async def save(self, store: HouseRuleStore, user_id: int) -> str:
        c = self.campaign
        changed = await store.edit(
            c.guild_id,
            c.id,
            user_id,
            self.existing.number,
            self.rule.value,
            self.instead.value,
            unchanged_since=self.existing.version,
        )
        return f"✏️ Changed house rule {changed.number}."

    def refusal(self, message: str) -> str:
        n = self.existing.number
        if message == house.GONE:
            return (
                "Another DM removed that house rule while you were editing, so your change "
                f"wasn't saved. To keep it, press **{ADD_LABEL[2:]}** and paste your words."
            )
        if message == house.CHANGED:
            return (
                "Another DM changed that house rule while you were editing, so your change "
                f"wasn't saved. Press **{EDIT_LABEL} {n}** to see their version, then paste "
                "your words if you still want yours."
            )
        if message == house.EMPTY:
            return f"{message} Press **{EDIT_LABEL} {n}** again and type the rule."
        return message


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
            await _tell(interaction, CAMPAIGN_GONE)
            return
        self.stop()
        await _redraw(interaction, campaign)


@dmbot_group.command(
    name="houserules",
    description="See this campaign's house rules (its DM can add, edit or remove them)",
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
