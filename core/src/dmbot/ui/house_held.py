"""A button on each house rule the file read held back for a clash (#3841).

When a rule in a house-rules file may clash with a rule already saved, the file read leaves it
out and says so (`house_file_sync.apply`). Before this the DM had to retype it. Here each held-back
rule gets a button that opens the same "which one applies" question as a typed rule
(`house_conflicts.DisagreeView`) with that rule's words filled in: replace the old rule, keep both
and say which wins, or cancel. Nothing is saved until the DM picks, and DMbot never picks. The DM
check is made on every press, from the campaign as it is now.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import house
from dmbot.rules.conflicts import Checked, Disagreement
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.rules.house_file_sync import ADD, SCENARIO, Held
from dmbot.ui import house_conflicts
from dmbot.ui.dmbot_commands import _Button, _Menu, _tell

MAX_BUTTONS = 15  # three rows of five; the rest are named in the text and added by hand
TAKEN = "That rule already has a question open, or it was answered."
NO_CLASH = "That rule doesn't clash with your house rules any more. Add it in `/dmbot houserules`."
ALREADY = "That rule is already in your house rules."
LABEL_MAX = 80  # Discord's limit for a button label


class HeldView(_Menu):
    """One button for each held-back rule of one file read."""

    def __init__(
        self,
        bot: Any,
        campaign: Campaign,
        held: Sequence[Held],
        *,
        source: str,
        source_name: str | None,
    ) -> None:
        super().__init__()
        self.bot, self.campaign, self.source, self.source_name = bot, campaign, source, source_name
        self.held = list(held)[:MAX_BUTTONS]
        # File numbers with a question open or answered: one at a time, and an answered rule is
        # never added a second time by pressing its button again (a cancel frees it).
        self.taken: set[int] = set()
        for h in self.held:
            verb = "Add" if h.item.kind == ADD else "Change"
            self.add_item(
                _Button(
                    self._press_for(h),
                    label=f"{verb} rule {h.item.number}…"[:LABEL_MAX],
                    style=discord.ButtonStyle.secondary,
                )
            )

    def _press_for(self, h: Held) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def press(interaction: discord.Interaction) -> None:
            await self.open(interaction, h)

        return press

    async def on_timeout(self) -> None:
        # The result text stays; only the buttons go (the base menu would blank the message).
        if self.origin is not None:
            with contextlib.suppress(discord.HTTPException):
                await self.origin.edit_original_response(view=None)

    async def open(self, interaction: discord.Interaction, h: Held) -> None:
        """Show the question for one held-back rule. DM only, read from the campaign now."""
        bot, c = self.bot, self.campaign
        store = getattr(bot, "campaigns", None)
        if store is not None:
            fresh = await store.get(c.guild_id, c.id)
            if fresh is None:
                await _tell(interaction, house_conflicts.GONE)
                return
            c = self.campaign = fresh
        if interaction.user.id not in c.dm_user_ids:
            await _tell(interaction, house_conflicts.ONLY_DMS)
            return
        rules = getattr(bot, "house_rules", None)
        if rules is None:
            await _tell(interaction, "House rules aren't ready yet. Try again in a moment.")
            return
        item, user_id = h.item, interaction.user.id
        if item.number in self.taken:
            await _tell(interaction, TAKEN)
            return
        # The rules it clashed with, as they are now: one edited or removed since the read is
        # compared again, and a question about words that no longer clash is not asked.
        try:
            now_there = await house_conflicts.existing_rules(bot, c)
        except Exception:
            await _tell(interaction, "I couldn't read your house rules just now. Try again.")
            return
        others = [o for o in h.others if o in now_there]
        if not others:
            await _tell(interaction, NO_CLASH)
            return
        self.taken.add(item.number)
        if item.kind == ADD:

            async def save() -> HouseRule:
                if any(
                    (r.rule, r.supersedes) == (item.rule, item.instead)
                    for r in await rules.list(c.guild_id, c.id)
                ):
                    raise HouseRuleError(ALREADY)  # saved by another press or another DM
                added_rule: HouseRule = await rules.add(
                    c.guild_id, c.id, user_id, item.rule, item.instead,
                    scenario=SCENARIO, wanted=item.number,
                    source=self.source, source_name=self.source_name,
                )  # fmt: skip
                return added_rule

            async def replace(number: int) -> str:
                current = next(
                    (r for r in await rules.list(c.guild_id, c.id) if r.number == number), None
                )
                if current is None:
                    raise HouseRuleError(house.GONE)
                changed = await rules.edit(
                    c.guild_id, c.id, user_id, number, item.rule,
                    item.instead or current.supersedes, unchanged_since=current.version,
                )  # fmt: skip
                return f"🔁 House rule {changed.number} now says this."

            def added(rule: HouseRule) -> str:
                return f"➕ Added house rule {rule.number}."

            saved_note: Callable[[HouseRule], str] = added
            replacer: Callable[[int], Awaitable[str]] | None = replace
        else:

            async def save() -> HouseRule:
                changed_rule: HouseRule = await rules.edit(
                    c.guild_id, c.id, user_id, item.number, item.rule, item.instead,
                    unchanged_since=item.version,
                )  # fmt: skip
                return changed_rule

            def changed(rule: HouseRule) -> str:
                return f"✏️ Changed house rule {rule.number}."

            saved_note = changed
            replacer = None  # a change has its own rule: nothing else to replace
        found = Checked(tuple(Disagreement(1, other) for other in others))
        await house_conflicts.ask(
            interaction,
            bot,
            c,
            item.rule + (f" (instead of: {item.instead})" if item.instead else ""),
            found,
            save=save,
            saved_note=saved_note,
            replace=replacer,
            done=self._answered,
            cancelled=lambda: self.taken.discard(item.number),
        )

    async def _answered(self, interaction: discord.Interaction, note: str) -> None:
        """The question is answered: say so on the question itself (a private message of its own),
        then send the file as it is now, as every other house-rule change does."""
        await interaction.response.edit_message(content=note, view=None)
        from dmbot.ui import house_file as file_ui  # (that module leads back here)

        await file_ui.send_after_change(interaction, self.campaign, note)
