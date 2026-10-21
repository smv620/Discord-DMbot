"""Asking the DM which of two house rules wins (#2284), when a rule they save disagrees with one.

`look` compares the words being saved with the campaign's own house rules and the ones it follows
(`dmbot.rules.conflicts`). On a disagreement `DisagreeView` shows both rules and four buttons:
**Replace rule N**, **New rule wins**, **Rule N wins** and **Cancel**.
Nothing is saved until the DM presses one, and DMbot never picks. A check that could not run (no
AI key, the service down) lets the rule be saved and says so; it never blocks a save.

The forms and buttons that save a rule (typed, voice, file) all come through here, so there is one
wording and one place that records the answer (`dmbot.rules.wins`).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import conflicts
from dmbot.rules.conflicts import Checked, Existing
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.wins import RuleRef
from dmbot.ui.dmbot_commands import _Button, _Menu, _tell

if TYPE_CHECKING:
    from dmbot.rules.wins import WinsStore

log = logging.getLogger(__name__)

UNCHECKED = (
    " _Couldn't check for clashes with your other house rules. Saved anyway; look them over in "
    "`/dmbot houserules`._"
)
ONLY_DMS = "Only this campaign's DMs can answer this."
WORDS_MAX = 150
LABEL_MAX = 25  # a phone cuts a longer label


def _words(rule: HouseRule) -> str:
    return rule.rule + (f" (instead of: {rule.supersedes})" if rule.supersedes else "")


async def existing_rules(bot: Any, campaign: Campaign, skip: int | None = None) -> list[Existing]:
    """The rules a new one is compared with: the campaign's own and the ones it follows.
    `skip`: the number of the own rule being edited, which is not compared with itself."""
    loaded: Sequence[HouseRule] = await bot.all_house_rules(campaign.guild_id, campaign.id)
    out: list[Existing] = []
    for rule in loaded:
        from_set = getattr(rule, "set_name", None) or None
        if from_set is None and rule.number == skip:
            continue
        out.append(Existing(rule.number, _words(rule), from_set))
    return out


async def look(
    bot: Any, campaign: Campaign, texts: Sequence[str], *, skip: int | None = None
) -> Checked:
    """Do the new rules disagree with a rule already in force? No AI, or nothing to compare
    with, is no check and no note."""
    ai = getattr(bot, "rule_conflict_ai", None)
    if ai is None or not any(t.strip() for t in texts):
        return Checked()
    try:
        existing = await existing_rules(bot, campaign, skip)
    except Exception:
        log.exception("Couldn't read the house rules to check for a disagreement")
        return Checked(unchecked=True)
    return await conflicts.check(ai.complete, texts, existing)


def ref_of(other: Existing) -> RuleRef:
    return RuleRef(other.from_set or "", other.number)


def name_of(other: Existing) -> str:
    return f"{other.from_set} rule {other.number}" if other.from_set else f"rule {other.number}"


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class DisagreeView(_Menu):
    """Both rules, and the DM's choice. `save` stores the new rule (and returns it); `replace`
    puts the new words in an own rule of the campaign (None: not offered); `done` shows the
    outcome where the flow began. `cancelled` runs when nothing was saved (the caller gives back
    what it had claimed)."""

    def __init__(
        self,
        bot: Any,
        campaign: Campaign,
        words: str,
        others: Sequence[Existing],
        *,
        save: Callable[[], Awaitable[HouseRule]],
        saved_note: Callable[[HouseRule], str],
        replace: Callable[[int], Awaitable[str]] | None,
        done: Callable[[discord.Interaction, str], Awaitable[None]],
        cancelled: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.bot, self.campaign, self.words, self.others = bot, campaign, words, list(others)
        self._save, self._saved_note, self._replace = save, saved_note, replace
        self._done, self._cancelled = done, cancelled
        self.at = 0
        self.saved: HouseRule | None = None
        self._buttons()

    @property
    def other(self) -> Existing:
        return self.others[self.at]

    def prompt(self) -> str:
        o = self.other
        count = f" (clash {self.at + 1} of {len(self.others)})" if len(self.others) > 1 else ""
        return (
            f"⚠️ **Your new rule may clash with {name_of(o)}{count}. Which one wins?**\n"
            f"**New:** {_fit(self.words, WORDS_MAX)}\n"
            f"**{name_of(o).capitalize()}:** {_fit(o.words, WORDS_MAX)}\n"
            "Both rules stay unless you replace the old one. You decide; DMbot never picks."
        )

    def _buttons(self) -> None:
        self.clear_items()
        o = self.other
        if self._replace is not None and o.from_set is None and self.saved is None:
            self.add_item(
                _Button(
                    self._on_replace,
                    label=f"Replace rule {o.number}",
                    style=discord.ButtonStyle.danger,
                )
            )
        self.add_item(
            _Button(
                self._new_wins,
                label="New rule wins",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(
            _Button(
                self._other_wins,
                label=_fit(
                    f"Rule {o.number} wins" if o.from_set is None else f"Set rule {o.number} wins",
                    LABEL_MAX,
                ),
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(_Button(self._on_cancel, label="Cancel", style=discord.ButtonStyle.secondary))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await super().interaction_check(interaction)
        if interaction.user.id in self.campaign.dm_user_ids:
            return True
        await _tell(interaction, ONLY_DMS)
        return False

    async def on_timeout(self) -> None:
        if self.saved is None and self._cancelled is not None:
            self._cancelled()
        await super().on_timeout()

    async def _end(self, interaction: discord.Interaction, note: str) -> None:
        self.stop()
        await self._done(interaction, note)

    async def _on_replace(self, interaction: discord.Interaction) -> None:
        assert self._replace is not None
        try:
            note = await self._replace(self.other.number)
        except HouseRuleError as exc:
            self.stop()
            if self._cancelled is not None:
                self._cancelled()
            await _tell(interaction, str(exc))
            return
        await self._end(interaction, note)

    async def _new_wins(self, interaction: discord.Interaction) -> None:
        await self._keep_both(interaction, new_wins=True)

    async def _other_wins(self, interaction: discord.Interaction) -> None:
        await self._keep_both(interaction, new_wins=False)

    async def _keep_both(self, interaction: discord.Interaction, *, new_wins: bool) -> None:
        try:
            if self.saved is None:
                self.saved = await self._save()
        except HouseRuleError as exc:
            self.stop()
            if self._cancelled is not None:
                self._cancelled()
            await _tell(interaction, str(exc))
            return
        mine, theirs = RuleRef("", self.saved.number), ref_of(self.other)
        winner, loser = (mine, theirs) if new_wins else (theirs, mine)
        wins: WinsStore | None = getattr(self.bot, "house_wins", None)
        if wins is not None:
            try:
                await wins.record(
                    self.campaign.guild_id, self.campaign.id, winner, loser, interaction.user.id
                )
            except Exception:
                log.exception("Couldn't store which house rule wins")
        self.at += 1
        if self.at < len(self.others):
            self._buttons()
            await interaction.response.edit_message(content=self.prompt(), view=self)
            return
        await self._end(interaction, self._summary())

    def _summary(self) -> str:
        assert self.saved is not None
        return (
            f"{self._saved_note(self.saved)} Both rules stay, and your choice is remembered. "
            "Change it any time with Edit or Remove."
        )

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if self.saved is None:
            if self._cancelled is not None:
                self._cancelled()
            await self._end(interaction, "Cancelled. Nothing was saved.")
            return
        await self._end(
            interaction,
            f"{self._saved_note(self.saved)} The other clashes were left unanswered.",
        )


async def ask(
    interaction: discord.Interaction,
    bot: Any,
    campaign: Campaign,
    words: str,
    found: Checked,
    *,
    save: Callable[[], Awaitable[HouseRule]],
    saved_note: Callable[[HouseRule], str],
    replace: Callable[[int], Awaitable[str]] | None,
    done: Callable[[discord.Interaction, str], Awaitable[None]],
    cancelled: Callable[[], None] | None = None,
) -> None:
    """Show the DM both rules privately. The caller has already answered Discord."""
    others: list[Existing] = []
    for d in found.disagreements:
        if d.other not in others:
            others.append(d.other)
    view = DisagreeView(
        bot,
        campaign,
        words,
        others,
        save=save,
        saved_note=saved_note,
        replace=replace,
        done=done,
        cancelled=cancelled,
    )
    with contextlib.suppress(discord.HTTPException):
        await _tell(interaction, view.prompt(), view=view)
