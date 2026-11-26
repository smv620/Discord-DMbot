"""✍️ Say a connection in your own words (#2940, slice B): the DM writes about two names and DMbot
reads it into connections to add or to correct one, which the DM confirms.

Two ways in, one flow: **✍️ Correct it** on a connection's page (the connection is replaced by what
the words say) and **✍️ Connect in words…** on a name's card (the DM picks the other name, as for
🧭 Connect to…). The words go in a form, DMbot reads them once (`memory/connection_said.py`; ten
reads an hour for a campaign) and answers **DMbot read this as:** with each connection as one plain
sentence built from its fields, who knows it, and **Use this**, **Write it again** and **Cancel**.
Nothing is written before **Use this**. Every press checks the campaign and that the person is one
of its DMs; **Use this** checks the names and the old connection again. The DM's words are used for
that one read and never stored or logged here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import fit_message
from dmbot.memory import connection_said as said
from dmbot.memory import link_edits
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, Relation
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.name_card import _names, show_card
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets

log = logging.getLogger(__name__)

ONLY_DMS = "Only this campaign's DMs can do that."
NO_AI = "DMbot can't read that just now. Try again in a little while."
NOT_ON_PLAN = "Reading your own words needs a plan that includes DMbot's AI."
WORKING = "⏳ DMbot is reading what you wrote…"
READ_AS = "**DMbot read this as:**"
FORM_TITLE = "Say it in your own words"
CORRECT_BUTTON = "✍️ Correct it"
CONNECT_BUTTON = "✍️ Connect in words…"
KEEP_WHO = "keep"  # "who knows" left as the connection being corrected had it
WHO_ALL, WHO_DM, WHO_SOME = "everyone", "dm", "some"
GONE = "That connection or one of those names isn't there any more. Open the name card again."
_LIMIT_ATTR = "connection_said_limit"


def _limit(bot: Any) -> said.ReadLimit:
    """The bot's one limiter (made on first use, so a build without it still works)."""
    found = getattr(bot, _LIMIT_ATTR, None)
    if found is None:
        found = said.ReadLimit()
        setattr(bot, _LIMIT_ATTR, found)
    return found


def sentence(first: str, second: str, kind: str, words: str, subject_kind: str = "") -> str:
    """One proposal as a plain sentence, built from its fields: "Mara “owes” Tavin"
    (`connection_said.plain_sentence`, the website's words too). The names and words are escaped,
    so a name can never format the message."""
    return _md(said.plain_sentence(first, second, kind, words, subject_kind))


@dataclass(frozen=True, slots=True)
class Pair:
    """What a read is about, as one press found it: the campaign, the two entries and (for a
    correction) the connection being replaced, as the DM saw it."""

    campaign: Campaign
    names: CampaignLookup
    first: said.End
    second: said.End
    replacing: Relation | None


async def _pair(
    interaction: discord.Interaction,
    campaign_id: str,
    first_id: str,
    second_id: str,
    replacing_id: str | None,
) -> Pair | None:
    """Everything a press needs, checked now: the campaign, that this person is one of its DMs, both
    entries and the connection to replace (still there and confirmed). Tells them if not."""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return None
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return None
    memory = _memory(interaction)
    names = await _names(interaction, campaign)
    if memory is None or names is None:
        return None
    first, second = said.end_from(names, first_id), said.end_from(names, second_id)
    replacing = None
    if replacing_id is not None:
        replacing = await memory.relation(campaign.guild_id, campaign.id, replacing_id)
        if replacing is None or replacing.status != CONFIRMED:
            first = None
    if first is None or second is None or first.id == second.id:
        await _tell(interaction, GONE)
        return None
    return Pair(campaign, names, first, second, replacing)


class SayForm(discord.ui.Modal, title=FORM_TITLE):
    """The DM's words about two names."""

    text: discord.ui.TextInput[SayForm] = discord.ui.TextInput(
        label="What is true about these two?",
        style=discord.TextStyle.paragraph,
        placeholder="For example: Mara owes Tavin the favour, not the other way round. "
        "Only Tavin knows.",
        max_length=said.TEXT_MAX,
    )

    def __init__(
        self,
        campaign_id: str,
        first_id: str,
        second_id: str,
        back_id: str,
        *,
        replacing_id: str | None = None,
        before: str = "",
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id, self.first_id, self.second_id = campaign_id, first_id, second_id
        self.back_id, self.replacing_id = back_id, replacing_id
        self.text.default = before or None

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await read_and_ask(
            interaction, self.campaign_id, self.first_id, self.second_id, self.back_id,
            self.text.value, replacing_id=self.replacing_id,
        )  # fmt: skip


async def open_form(
    interaction: discord.Interaction,
    campaign_id: str,
    first_id: str,
    second_id: str,
    back_id: str,
    *,
    replacing_id: str | None = None,
) -> None:
    """Show the form, if this person may. (A form must open within three seconds, so the check is
    the campaign and the DM only; the entries are checked when it is sent.)"""
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    if not sees_secrets(campaign, interaction.user.id):
        await _tell(interaction, ONLY_DMS)
        return
    await interaction.response.send_modal(
        SayForm(campaign_id, first_id, second_id, back_id, replacing_id=replacing_id)
    )


async def read_and_ask(
    interaction: discord.Interaction,
    campaign_id: str,
    first_id: str,
    second_id: str,
    back_id: str,
    text: str,
    *,
    replacing_id: str | None = None,
) -> None:
    """The form was sent: check, read once, and show what DMbot understood for the DM to confirm."""
    await _answer_first(interaction, in_place=True)
    pair = await _pair(interaction, campaign_id, first_id, second_id, replacing_id)
    if pair is None:
        return
    bot: Any = interaction.client
    ai = getattr(bot, "connection_said_ai", None)
    if ai is None:
        await _tell(interaction, NO_AI)
        return
    c = pair.campaign
    allows = getattr(bot, "plan_allows", None)
    # (a plan that can't be checked is a no: the AI is paid for by the plan)
    if allows is None or not await allows("ai", c.guild_id, c, fail_open=False):
        await _tell(interaction, NOT_ON_PLAN)
        return
    if not text.strip() or len(" ".join(text.split())) > said.TEXT_MAX:
        await _tell(interaction, said.NOTHING if not text.strip() else said.TOO_LONG)
        return  # (nothing was asked of the AI, so no read is counted)
    wait, token = _limit(bot).claim(c.guild_id, c.id)
    if wait:
        await _tell(
            interaction,
            f"That's {said.READS_PER_HOUR} reads this hour. Try again in {wait} minutes.",
        )
        return
    # Every wait shows that DMbot is working (CLAUDE.md): said before the read starts.
    await _replace(interaction, WORKING, None)
    got = await said.read(ai.complete, pair.first, pair.second, text)
    if said.no_answer(got):
        _limit(bot).give_back(c.guild_id, c.id, token)  # the AI was down: not the DM's read
    try:
        view = ConfirmSaid(interaction, pair, back_id, text, got)
        await view.show(interaction)
    except Exception:
        log.exception("Couldn't show a connection read in words")
        await _replace(interaction, "Something went wrong. Nothing was changed.", None)


class ConfirmSaid(_Menu):
    """**DMbot read this as:** each connection as a plain sentence, who knows it, and the DM's
    choice. Nothing has been written."""

    def __init__(
        self,
        origin: discord.Interaction,
        pair: Pair,
        back_id: str,
        text: str,
        got: said.Said,
    ) -> None:
        super().__init__()
        self.pair, self.back_id, self.text, self.got = pair, back_id, text, got
        self._busy = asyncio.Lock()  # two presses at once must not write twice
        self.done = False
        self.characters, self.left_out = said.characters_named(pair.names, got.who.names)
        # Who knows: what the words said; else, for a correction of a hidden one, as it was
        hidden_before = pair.replacing is not None and (
            pair.replacing.secret or pair.replacing.unrevealed
        )
        restricted = got.who.kind == said.SOME and not self.characters  # (named, none matched)
        if got.who.kind == said.ONLY_DM or any(p.secret for p in got.proposals):
            self.who = WHO_DM
        elif got.who.kind == said.SOME and self.characters:
            self.who = WHO_SOME
        elif hidden_before:
            self.who = KEEP_WHO
        elif restricted:
            self.who = WHO_DM  # the DM meant it kept back: never widened to everyone by a miss
        else:
            self.who = WHO_ALL
        self.kept = list(range(len(got.proposals)))
        self._buttons()

    def _name(self, entry_id: str) -> str:
        return self.pair.first.name if entry_id == self.pair.first.id else self.pair.second.name

    def _subject_kind(self, entry_id: str) -> str:
        entity = self.pair.names.entities.get(entry_id)
        return entity.type if entity is not None else ""

    def sentences(self) -> list[str]:
        return [
            sentence(
                self._name(p.first),
                self._name(p.second),
                p.kind,
                p.words,
                self._subject_kind(p.first),
            )
            for p in self.got.proposals
        ]

    def who_line(self) -> str:
        if self.who == WHO_DM:
            return "Who knows it: **only you, the DM**."
        if self.who == WHO_SOME:
            who = ", ".join(_md(self.pair.names.entities[i].name) for i in self.characters)
            return f"Who knows it: **only {who}**."
        if self.who == KEEP_WHO:
            return "Who knows it: **as it was before**."
        return "Who knows it: **everyone at the table**."

    def text_to_show(self) -> str:
        if not self.got.proposals:
            return f"{self.got.problem or said.NOTHING}\nWrite it again, or cancel."
        lines = self.sentences()
        body = "\n".join(f"• {s}" for s in lines)
        replace = (
            "\nUse this replaces the connection you are correcting. You can undo it."
            if self.pair.replacing is not None
            else "\nYou can undo it."
        )
        left = ""
        if self.left_out:
            said_names = ", ".join(f"“{_md(n)}”" for n in self.left_out)
            left = f"\nI couldn't tell who {said_names} is, so I left that out."
            if self.who == WHO_DM:
                left += " It stays with you alone unless you change it below."
        return f"{READ_AS}\n{body}\n{self.who_line()}{left}{replace}"

    def _buttons(self) -> None:
        self.clear_items()
        grey, green = discord.ButtonStyle.secondary, discord.ButtonStyle.success
        if self.got.proposals:
            if len(self.got.proposals) > 1:
                options = [
                    discord.SelectOption(
                        label=logic.shorten(s, logic.OPTION_LABEL_MAX),
                        value=str(i),
                        default=i in self.kept,
                    )
                    for i, s in enumerate(self.sentences())
                ]
                self.pick = _Select(
                    self._picked, placeholder="Which of these?", options=options,
                    min_values=1, max_values=len(options), row=0,
                )  # fmt: skip
                self.add_item(self.pick)
            self.add_item(self._who_select())
            self.add_item(_Button(self._use, label="✅ Use this", style=green, row=2))
        self.add_item(_Button(self._again, label="✍️ Write it again", style=grey, row=2))
        self.add_item(_Button(self._cancel, label="Cancel", style=grey, row=2))

    def _who_select(self) -> _Select:
        options = [
            discord.SelectOption(
                label="Everyone at the table", value=WHO_ALL, default=self.who == WHO_ALL
            ),
            discord.SelectOption(label="Only me, the DM", value=WHO_DM, default=self.who == WHO_DM),
        ]
        if self.characters:
            names = ", ".join(self.pair.names.entities[i].name for i in self.characters)
            options.append(
                discord.SelectOption(
                    label=logic.shorten(f"Only {names}", logic.OPTION_LABEL_MAX),
                    value=WHO_SOME,
                    default=self.who == WHO_SOME,
                )
            )
        if self.pair.replacing is not None and (
            self.pair.replacing.secret or self.pair.replacing.unrevealed
        ):
            options.append(
                discord.SelectOption(
                    label="As it was before", value=KEEP_WHO, default=self.who == KEEP_WHO
                )
            )
        return _Select(self._who_picked, placeholder="Who knows this?", options=options, row=1)

    async def show(self, interaction: discord.Interaction) -> None:
        await _replace(interaction, fit_message(self.text_to_show()), self)

    async def _redraw(self, interaction: discord.Interaction) -> None:
        self._buttons()
        await interaction.response.edit_message(content=fit_message(self.text_to_show()), view=self)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.kept = sorted(int(v) for v in self.pick.values)
        # the choice is kept; the sentences stay as they are, the unticked ones are left out
        for option in self.pick.options:
            option.default = option.value in self.pick.values
        await interaction.response.defer()

    async def _who_picked(self, interaction: discord.Interaction) -> None:
        select = next(
            c
            for c in self.children
            if isinstance(c, _Select) and c.placeholder == "Who knows this?"
        )
        self.who = select.values[0]
        await self._redraw(interaction)

    async def _again(self, interaction: discord.Interaction) -> None:
        # the form can be opened only from a press, and shows the words as they were
        c = self.pair.campaign
        campaign = await _campaign_for(interaction, c.id)
        if campaign is None or not sees_secrets(campaign, interaction.user.id):
            if campaign is not None:
                await _tell(interaction, ONLY_DMS)
            return
        self.origin = None  # the form's answer replaces this message; cancelling it keeps it
        await interaction.response.send_modal(
            SayForm(
                c.id, self.pair.first.id, self.pair.second.id, self.back_id,
                replacing_id=self.pair.replacing.id if self.pair.replacing else None,
                before=self.text,
            )
        )  # fmt: skip

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.pair.campaign.id, self.back_id, replace=True)

    async def _use(self, interaction: discord.Interaction) -> None:
        async with self._busy:
            if self.done:
                await _tell(interaction, "That's already done.")
                return
            await self._use_once(interaction)

    async def _use_once(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        c = self.pair.campaign
        # the campaign, the DM and both names are checked again, and the old connection is read
        # again: if it changed since, nothing is written (apply)
        again = await _pair(
            interaction, c.id, self.pair.first.id, self.pair.second.id,
            self.pair.replacing.id if self.pair.replacing else None,
        )  # fmt: skip
        memory = _memory(interaction)
        if again is None or memory is None:
            return
        chosen = [self.got.proposals[i] for i in self.kept]
        if not chosen:
            await _tell(interaction, "Pick at least one.")
            return
        who: str | None = (
            None
            if self.who == KEEP_WHO
            else {
                WHO_ALL: link_edits.EVERYONE,
                WHO_DM: link_edits.ONLY_THE_DM,
                WHO_SOME: link_edits.SOME_CHARACTERS,
            }[self.who]
        )
        done = await said.apply(
            memory, c.guild_id, c.id, chosen, who=who,
            characters=self.characters if self.who == WHO_SOME else (),
            replacing=self.pair.replacing,  # as the DM saw it: apply refuses if it changed since
        )  # fmt: skip
        if done.problem:
            await _tell(interaction, done.problem)
            return
        self.done = True
        self.stop()
        changed(interaction, c)
        lines = [
            sentence(
                self._name(p.first),
                self._name(p.second),
                p.kind,
                p.words,
                self._subject_kind(p.first),
            )
            for p in chosen
        ]
        note = "✅ " + ("Corrected" if again.replacing is not None else "Added") + ":\n"
        note += "\n".join(f"• {line}" for line in lines)
        await _replace(
            interaction, note + "\nWrong? Press **Undo** within 10 minutes.",
            Done(self.pair, self.back_id, done),
        )  # fmt: skip


class Done(_Menu):
    """After **Use this**: ↩️ Undo takes the whole thing back at once, and Back goes to the card."""

    def __init__(self, pair: Pair, back_id: str, applied: said.Applied) -> None:
        super().__init__()
        self.pair, self.back_id, self.applied = pair, back_id, applied
        self.add_item(_Button(self._undo, label="↩️ Undo", style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        c = self.pair.campaign
        campaign = await _campaign_for(interaction, c.id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, ONLY_DMS)
            return
        self.stop()
        whole = await said.undo(memory, c.guild_id, c.id, self.applied)
        changed(interaction, c)
        note = "↩️ Undone." if whole else "↩️ Undone, except part that changed since."
        await show_card(interaction, c.id, self.back_id, replace=True, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.pair.campaign.id, self.back_id, replace=True)
