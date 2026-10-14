"""A followed house-rules set changed (#1191): what happens at a session's start, and the note.

At the start of a session (never during one, so a table's rules don't change mid-game) DMbot
asks whether the set the campaign follows has a newer version. If it does, DMbot **takes it
straight away** (Follow is what happens when the DM does nothing, and the take is credited to
DMbot, not to a DM) and posts one note on the DM screen: "<set> house rules updated to v5:
[See changes] [Pick changes] [Undo]".

- **Undo** goes back to the copy the campaign had. It is kept in the follow's own row, so the
  button works after a restart.
- **Pick changes** undoes the take and opens the per-change picker in one step. Everything
  starts ticked. Unticking anything copies the set (as the campaign had it plus the accepted
  changes) into the campaign's own house rules, marked with where it came from, ranked below
  the rules the campaign wrote itself, and ends the follow. That is one operation with one
  Undo.
- **See changes** lists what differs from the copy before.

A press during play is the DM's own choice and is allowed. Every press checks again that the
person is one of the campaign's DMs and that the button is for the version the campaign is on,
so an old button can never undo or change a newer update. Buttons survive a restart
(`bot.add_dynamic_items(SetUpdateButton)`).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import rule_sets, set_diff
from dmbot.rules.house import HouseRuleError
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _Select, _tell

log = logging.getLogger(__name__)

PAGE = 25  # a Discord menu holds 25 choices
TEXT_MAX = 1900
ONLY_DMS = "Only this campaign's DMs can do that."
GONE = "That campaign isn't here any more."
NEWER = (
    "A newer version came after that note. Look for the newest note on the DM screen. "
    "Nothing changed."
)
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
UNDO_LABEL = "↩️ Undo"


def note_text(follow_name: str, version: int, changes: int) -> str:
    rules = f"{changes} rule" + ("" if changes == 1 else "s")
    return (
        f"📚 **{discord.utils.escape_markdown(follow_name)}** house rules updated to "
        f"v{version}: {rules}. DMbot took the new version."
    )


def note_view(campaign_id: str, version: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("see", "pick", "undo"):
        view.add_item(SetUpdateButton(campaign_id, action, version))
    return view


_LABELS = {
    "see": ("See changes", discord.ButtonStyle.secondary),
    "pick": ("Pick changes", discord.ButtonStyle.secondary),
    "undo": ("Undo", discord.ButtonStyle.secondary),
}


class SetUpdateButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:setupd:(?P<action>see|pick|undo):(?P<campaign>[0-9a-f]{32}):(?P<version>[0-9]{1,9})",
):
    def __init__(self, campaign_id: str, action: str, version: int) -> None:
        label, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"dmbot:setupd:{action}:{campaign_id}:{version}",
            )
        )
        self.campaign_id, self.action, self.version = campaign_id, action, version

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> SetUpdateButton:
        return cls(match["campaign"], match["action"], int(match["version"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.action, self.version)
        except Exception:
            log.exception("A shared-rules update button failed")
            await _tell(interaction, COULDNT)


async def _dm_and_follow(
    interaction: discord.Interaction, campaign_id: str
) -> tuple[Campaign, rule_sets.Follow] | None:
    """The campaign and its followed set, if this person is one of its DMs now."""
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
    follows = [
        f for f in await sets.following(campaign.guild_id, campaign.id) if f.kind == "house_rules"
    ]
    if not follows:
        await _tell(interaction, NEWER)
        return None
    return campaign, follows[0]


async def press(
    interaction: discord.Interaction, campaign_id: str, action: str, version: int
) -> None:
    await _answer_first(interaction)
    found = await _dm_and_follow(interaction, campaign_id)
    if found is None:
        return
    campaign, follow = found
    sets = _bot(interaction).rule_sets
    assert sets is not None  # checked above
    if follow.version != version:  # the note is for an older version than the campaign has
        await _tell(interaction, NEWER)
        return
    if action == "see":
        before = follow.previous_content
        if before is None:
            await _tell(interaction, "There's nothing to compare: that update was undone.")
            return
        await _tell(
            interaction, changes_text(follow.name, version, set_diff.diff(before, follow.content))
        )
        return
    try:
        await sets.undo_last_take(campaign.guild_id, campaign.id, interaction.user.id, version)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    if action == "undo":
        back = follow.previous_version
        await _tell(interaction, f"↩️ Undone. Back on v{back}, and still following.")
        return
    update = await sets.pending_update(campaign.guild_id, campaign.id)
    if update is None or update.offer.version != version:
        await _tell(interaction, NEWER)  # undone, and a different version is waiting now
        return
    view = PickView(campaign, update)
    await _tell(interaction, view.text(), view=view)
    view.origin = interaction


def changes_text(name: str, version: int, changes: list[set_diff.Change]) -> str:
    lines = [set_diff.describe(c) for c in changes]
    text = "\n".join(lines) or "Only the version number changed. No rule is different."
    if len(text) > TEXT_MAX:
        kept = text[:TEXT_MAX].rsplit("\n", 1)[0]
        left = len(lines) - kept.count("\n") - 1
        text = kept + f"\n…and {left} more. Use Pick changes to go through all of them."
    return f"**{discord.utils.escape_markdown(name)}** v{version}:\n{text}"


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
            expected_version=update.offer.version,  # the version the DM was shown
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    name = discord.utils.escape_markdown(update.offer.name)
    changed = {c.number for c in update.changes}
    left_out = len(changed - (accepted or changed))
    if taken.merged:
        words = (
            f"✅ Done. **{name}** is now your own copy ({len(taken.added)} rules, {left_out} "
            "changes left out). You no longer get its updates. Your own rules still win."
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
            kept = await bot.rule_sets.undo_take(
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
        edited = f" {kept} merged rule{'s' if kept != 1 else ''} you edited stayed." if kept else ""
        await _tell(interaction, f"↩️ Undone. Back on v{v}{again}.{edited}")


class PickView(_Menu):
    """Cherry pick: every change starts ticked (kept). Untick what the DM doesn't want, then
    Save my picks. Pages of 25, because a Discord menu holds no more."""

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
        found = await _dm_and_follow(interaction, self.campaign.id)
        if found is None:
            return
        self.stop()
        # The version the DM was shown, not whatever is newest now: if a newer one has come,
        # nothing is applied and the DM is told (the store compares the two).
        await take(interaction, self.campaign, self.update, set(self.accepted))


async def check_update(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """At a session's start: take a newer version of the followed set at once and say so on
    the DM screen. Never raises; a set whose publisher stopped sharing is marked "no longer
    updated" and said so once."""
    sets = bot.rule_sets
    if sets is None:
        return
    try:
        update = await sets.pending_update(campaign.guild_id, campaign.id)
        if update is None:
            if await sets.is_gone(campaign.guild_id, campaign.id):
                follows = await sets.following(campaign.guild_id, campaign.id)
                await sets.stop_updating(campaign.guild_id, campaign.id)
                name = discord.utils.escape_markdown(follows[0].name) if follows else "The set's"
                await bot.post(
                    screen_id,
                    f"📚 **{name}** house rules aren't shared any more. You keep your copy as it "
                    "is; it just won't get updates.",
                )
            return
        await sets.take_update(  # DMbot takes it: Follow is the default when the DM does nothing
            campaign.guild_id,
            campaign.id,
            None,
            None,
            expected_version=update.offer.version,
        )
        await bot.post_message(
            screen_id,
            note_text(update.offer.name, update.offer.version, len(update.changes)),
            note_view(campaign.id, update.offer.version),
        )
    except Exception:
        log.exception("Couldn't check for a shared-rules update")
