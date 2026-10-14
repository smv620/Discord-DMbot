"""A followed house-rules set changed (#1191): the note at a session's start and what its
buttons do.

At the start of a session (never during one, so a table's rules don't change mid-game) DMbot
asks whether the set the campaign follows has a newer version. If so the DM screen gets one
note: "The Red Table house rules changed (v4): 2 rules" with **Follow**, **Cherry pick** and
**See changes**. Follow takes the new version. Cherry pick lets the DM keep or leave each
change; leaving any ends the follow and copies the set (as the campaign had it, plus the
accepted changes) into the campaign's own house rules, marked "From <set> house rules, v4".
Every taking is one operation with an Undo. A DM who does nothing gets Follow at the next
session start. Nothing here reads anything of the publisher but the set.

The buttons on the note survive a restart (`bot.add_dynamic_items(SetUpdateButton)`); every
press checks again that the person is one of the campaign's DMs and that the update is still
waiting.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import rule_sets, set_diff
from dmbot.rules.house import HouseRuleError
from dmbot.ui.dmbot_commands import NO_PINGS, _answer_first, _bot, _Button, _Menu, _Select, _tell

log = logging.getLogger(__name__)

PAGE = 25  # a Discord menu holds 25 choices
TEXT_MAX = 1900
ONLY_DMS = "Only this campaign's DMs can do that."
GONE = "That campaign isn't here any more."
NOT_WAITING = (
    "That update is already done, or a newer one came. Look for the newest note on the "
    "DM screen. Your picks weren't saved."
)
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
UNDO_LABEL = "↩️ Undo"


def note_text(update: rule_sets.Update) -> str:
    return (
        f"📚 **{discord.utils.escape_markdown(update.offer.name)}** house rules changed "
        f"(v{update.offer.version}): {set_diff.count_words(update.changes)}. Still on "
        f"v{update.follow.version} for now. Choose below, or DMbot uses "
        f"v{update.offer.version} at your next session."
    )


def note_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("follow", "pick", "see"):
        view.add_item(SetUpdateButton(campaign_id, action))
    return view


_LABELS = {
    "follow": ("Use all changes", discord.ButtonStyle.primary),
    "pick": ("Pick changes", discord.ButtonStyle.secondary),
    "see": ("See changes", discord.ButtonStyle.secondary),
}


class SetUpdateButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:setupd:(?P<action>follow|pick|see):(?P<campaign>[0-9a-f]{32})",
):
    def __init__(self, campaign_id: str, action: str) -> None:
        label, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label, style=style, custom_id=f"dmbot:setupd:{action}:{campaign_id}"
            )
        )
        self.campaign_id, self.action = campaign_id, action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SetUpdateButton:
        return cls(match["campaign"], match["action"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.action)
        except Exception:
            log.exception("A shared-rules update button failed")
            await _tell(interaction, COULDNT)


async def _dm_and_update(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Campaign, rule_sets.Update] | None:
    """The campaign, if this person is one of its DMs now, and its waiting update."""
    bot = _bot(interaction)
    sets = bot.rule_sets
    if interaction.guild is None or sets is None:
        await _tell(interaction, COULDNT)
        return None
    campaign = await bot.campaigns.get(interaction.guild.id, campaign_id)
    if campaign is None:
        await _tell(interaction, GONE)
        return None
    if interaction.user.id not in campaign.dm_user_ids:
        await _tell(interaction, ONLY_DMS)
        return None
    update = await sets.pending_update(campaign.guild_id, campaign.id)
    if update is None:
        await _tell(interaction, NOT_WAITING)
        return None
    return campaign, update


async def press(interaction: discord.Interaction, campaign_id: str, action: str) -> None:
    await _answer_first(interaction)
    found = await _dm_and_update(interaction, campaign_id)
    if found is None:
        return
    campaign, update = found
    if action == "see":
        await _tell(interaction, changes_text(update), view=note_view(campaign.id))
    elif action == "pick":
        view = PickView(campaign, update)
        await _tell(interaction, view.text(), view=view)
        view.origin = interaction
    else:
        await take(interaction, campaign, update, None)


def changes_text(update: rule_sets.Update) -> str:
    lines = [set_diff.describe(c) for c in update.changes]
    text = "\n".join(lines) or "Only the version number changed. No rule is different."
    if len(text) > TEXT_MAX:
        kept = text[:TEXT_MAX].rsplit("\n", 1)[0]
        left = len(lines) - kept.count("\n") - 1
        text = kept + f"\n…and {left} more. Use Pick changes to go through all of them."
    return (
        f"**{discord.utils.escape_markdown(update.offer.name)}** v{update.offer.version}:\n{text}"
    )


async def take(
    interaction: discord.Interaction,
    campaign: Campaign,
    update: rule_sets.Update,
    accepted: set[int] | None,
) -> None:
    sets = _bot(interaction).rule_sets
    if sets is None:
        await _tell(interaction, COULDNT)
        return
    try:
        taken = await sets.take_update(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            accepted,
            expected_version=update.offer.version,
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    name = discord.utils.escape_markdown(update.offer.name)
    left_out = (
        len(update.changes) - len(accepted & {c.number for c in update.changes})
        if accepted
        else len(update.changes)
    )
    if taken.merged:
        words = (
            f"✅ Done. **{name}** is now your own copy ({len(taken.added)} rules, "
            f"{left_out} left out). You no longer get its updates. Your earlier rules still win."
        )
    else:
        words = f"✅ Now using **{name}** v{taken.after_version}."
    await _tell(interaction, words, view=UndoView(campaign, taken))


class UndoView(_Menu):
    def __init__(self, campaign: Campaign, taken: rule_sets.Taken) -> None:
        super().__init__()
        self.campaign, self.taken = campaign, taken
        self.add_item(_Button(self._undo, label=UNDO_LABEL))

    async def _undo(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        bot = _bot(interaction)
        current = await bot.campaigns.get(self.campaign.guild_id, self.campaign.id)
        if current is None or interaction.user.id not in current.dm_user_ids:
            await _tell(interaction, ONLY_DMS)
            return
        if bot.rule_sets is None:
            await _tell(interaction, COULDNT)
            return
        try:
            await bot.rule_sets.undo_take(
                current.guild_id, current.id, interaction.user.id, self.taken
            )
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        except Exception:
            log.exception("Couldn't undo a shared-rules update")
            await _tell(interaction, COULDNT)
            return
        self.stop()
        v = self.taken.before.version
        again = " and following it again" if self.taken.merged else " and still following it"
        await _tell(interaction, f"↩️ Undone. Back on v{v}{again}.")


class PickView(_Menu):
    """Cherry pick: every change starts ticked (kept). Untick what the DM doesn't want, then
    Apply. Pages of 25, because a Discord menu holds no more."""

    def __init__(self, campaign: Campaign, update: rule_sets.Update) -> None:
        super().__init__()
        self.campaign, self.update = campaign, update
        self.accepted = {c.number for c in update.changes}
        self.page = 0
        self._draw()

    def _page_changes(self) -> list[set_diff.Change]:
        return list(self.update.changes[self.page * PAGE : (self.page + 1) * PAGE])

    def text(self) -> str:
        pages = max(1, -(-len(self.update.changes) // PAGE))
        where = f" (page {self.page + 1} of {pages})" if pages > 1 else ""
        lines = "\n".join(set_diff.describe(c, 60) for c in self._page_changes())
        return (
            f"Pick changes: **{discord.utils.escape_markdown(self.update.offer.name)}**{where}. "
            "Ticked = keep. Everything starts ticked. If you untick any, you stop following "
            "the set and get your own copy. You can undo it.\n" + lines
        )

    def _draw(self) -> None:
        self.clear_items()
        shown = self._page_changes()
        if shown:
            options = [
                discord.SelectOption(
                    label=f"{c.number} {c.kind.capitalize()}"[:25],
                    description=set_diff.describe(c, 90)[:100],
                    value=str(c.number),
                    default=c.number in self.accepted,
                )
                for c in shown
            ]
            self.add_item(
                _Select(
                    self._picked,
                    placeholder="Changes to keep",
                    min_values=0,
                    max_values=len(options),
                    options=options,
                    row=0,
                )
            )
        if len(self.update.changes) > PAGE:
            self.add_item(_Button(self._prev, label="◀ Back", row=1, disabled=self.page == 0))
            last = (self.page + 1) * PAGE >= len(self.update.changes)
            self.add_item(_Button(self._next, label="More ▶", row=1, disabled=last))
        self.add_item(
            _Button(self._apply, label="Save my picks", style=discord.ButtonStyle.primary, row=1)
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        chosen = {int(v) for v in select.values}
        shown = {c.number for c in self._page_changes()}
        self.accepted = (self.accepted - shown) | chosen
        self._draw()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self._draw()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        self._draw()
        await interaction.response.edit_message(content=self.text(), view=self)

    async def _apply(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)
        found = await _dm_and_update(interaction, self.campaign.id)
        if found is None:
            return
        campaign, update = found
        self.stop()
        await take(interaction, campaign, update, set(self.accepted))


async def check_update(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """At a session's start: tell the DMs about a newer version of the set the campaign
    follows, once. An update they were told about and left alone is taken now (Follow is the
    default). Never raises; a set that is gone is marked "no longer updated"."""
    sets = bot.rule_sets
    if sets is None or not campaign.dm_user_ids:
        return
    try:
        update = await sets.pending_update(campaign.guild_id, campaign.id)
        if update is None:
            if await sets.is_gone(campaign.guild_id, campaign.id):
                await sets.stop_updating(campaign.guild_id, campaign.id)
                name = discord.utils.escape_markdown(
                    (await sets.following(campaign.guild_id, campaign.id))[0].name
                )
                await bot.post(
                    screen_id,
                    f"📚 **{name}** house rules aren't shared any more. You keep your copy as it "
                    "is; it just won't get updates.",
                )
            return
        if update.follow.notified is not None and update.follow.notified >= update.offer.version:
            await sets.take_update(campaign.guild_id, campaign.id, min(campaign.dm_user_ids))
            await bot.post(
                screen_id,
                f"📚 **{discord.utils.escape_markdown(update.offer.name)}** house rules "
                f"v{update.offer.version} are now in use. You hadn't chosen, so DMbot took the "
                "whole update.",
            )
            return
        posted = await bot.post_message(screen_id, note_text(update), note_view(campaign.id))
        if posted is not None:
            await sets.mark_notified(campaign.guild_id, campaign.id, update.offer.version)
    except Exception:
        log.exception("Couldn't check for a shared-rules update")


__all__ = ["NO_PINGS", "SetUpdateButton", "check_update"]
