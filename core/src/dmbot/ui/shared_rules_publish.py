"""The publisher's side of shared house rules (#1191): publishing a new version, undoing it,
and the version history. Only the campaign's DMs edit the set (no separate co-editor list; owner
decision, PLAN #1257). Reached from the Shared house rules menu in ⚙️ Settings; every press
re-checks that the person is a DM of the campaign. Followers get a new version at their next session
start, never sooner, and may take it or pick from it (dmbot.dm_screen.set_update)."""

from __future__ import annotations

from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import rule_sets, set_diff
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _send, _tell
from dmbot.ui.shared_rules import (
    CANCEL_LABEL,
    _escape,
    _guarded,
    _ready,
    _rules,
)

SHOWN_CHANGES = 12  # the lines of a diff quoted on the screen; the rest are counted
BODY_MAX = 1400  # the diff's characters: the whole message stays under Discord's 2000
YOUR_RULES_CHANGED = (
    "Your house rules changed while you were looking. Press 🔄 Publish my changes again to "
    "see the new changes."
)
NOT_SHARED_HERE = "Your rules aren't shared yet. Use 📤 Share my house rules first."
HISTORY_SHOWN = 8
LINK_ONLY = "Only the rules document link changed."
UNDO_LABEL = "↩️ Undo"
NOTHING_NEW = "Your house rules are the same as the shared version, so there is nothing to publish."


def changes_text(changes: list[set_diff.Change]) -> str:
    lines = [set_diff.describe(c, 100) for c in changes[:SHOWN_CHANGES]]
    more = len(changes) - len(lines)
    if more > 0:
        lines.append(f"…and {more} more.")
    text = "\n".join(_escape(line) for line in lines)
    if len(text) > BODY_MAX:
        text = text[:BODY_MAX].rsplit("\n", 1)[0] + "\n…and more."
    return text


async def _current_rules(
    interaction: discord.Interaction, current: Campaign
) -> tuple[list[Any], str | None] | None:
    bot = _bot(interaction)
    if bot.house_rules is None:
        await _tell(interaction, "Shared rules aren't available right now. Please try again.")
        return None
    rules = await bot.house_rules.list(current.guild_id, current.id)
    links = getattr(bot, "house_file_links", None)
    link = await links.get(current.guild_id, current.id) if links is not None else None
    return rules, getattr(link, "link", None)


@_guarded
async def show_changes(interaction: discord.Interaction, campaign: Campaign) -> None:
    """What would change if the campaign's house rules became the set's next version."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    # Asked before the diff, so nobody reads changes they can't publish (as Share does).
    refused = await _bot(interaction).plan_gate(
        "share", current.guild_id, current, interaction.user.id
    )
    if refused is not None:
        await _tell(interaction, refused)
        return
    shared = await store.current_content(current.guild_id, current.id)
    if shared is None:
        await _tell(interaction, NOT_SHARED_HERE)
        return
    mine, before = shared
    got = await _current_rules(interaction, current)
    if got is None:
        return
    rules, link = got
    after = rule_sets.content_of(rules, link)
    changes = set_diff.diff(before, after)
    if not changes and before.get("link") == after.get("link"):
        await _tell(interaction, NOTHING_NEW)
        return
    body = changes_text(changes)
    if before.get("link") != after.get("link"):
        body = f"{body}\nThe rules document link changed." if body else LINK_ONLY
    count = f"{set_diff.count_words(changes)} changed" if changes else "no rule changed"
    await _send(
        interaction,
        f"**{_escape(mine.name[:60])}**: publish as version {mine.version + 1}? "
        f"{count}:\n{body}\n"
        "Tables using it get the new version at their next session start.",
        ConfirmPublish(current, mine.version, after),
    )


class ConfirmPublish(_Menu):
    def __init__(self, campaign: Campaign, version: int, shown: dict[str, Any]) -> None:
        super().__init__()
        self.campaign, self.version, self.shown = campaign, version, shown
        self.add_item(_Button(self._yes, label="Yes, publish", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._no, label=CANCEL_LABEL))

    @_guarded
    async def _yes(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        bot = _bot(interaction)
        refused = await bot.plan_gate("share", current.guild_id, current, interaction.user.id)
        if refused is not None:
            await _tell(interaction, refused)
            return
        got = await _current_rules(interaction, current)
        if got is None:
            return
        rules, link = got
        shared = await store.current_content(current.guild_id, current.id)
        if shared is None:
            await _tell(interaction, NOT_SHARED_HERE)
            return
        if shared[0].version != self.version:
            await _tell(interaction, rule_sets.CHANGED_AGAIN)  # someone else published meanwhile
            return
        if rule_sets.content_of(rules, link) != self.shown:
            # What would be published is not what the DM saw: they look again first.
            await _tell(interaction, YOUR_RULES_CHANGED)
            return
        self.stop()
        made = await store.publish_changes(
            current.guild_id,
            current.id,
            interaction.user.id,
            rules,
            link,
            plan_ok=True,  # checked just above with plan_gate
            expected_version=self.version,  # and again under the lock, in the store
        )
        await _send(
            interaction,
            f"✅ **{_escape(made.name[:60])}** is now version {made.version} "
            f"({_rules(len(rules))}). Tables get it at their next session start.",
            UndoPublish(current, made.version),
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")


@_guarded
async def show_history(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Who made each version of the set (read only)."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    shared = await store.published(current.guild_id, current.id)
    if shared is None or not shared.shared:
        await _tell(interaction, NOT_SHARED_HERE)
        return
    bot = _bot(interaction)
    log = await store.version_log(current.guild_id, current.id)
    lines = [f"**{_escape(shared.name[:60])}**: versions"]
    for entry in log[:HISTORY_SHOWN]:
        how = {
            "publisher": "",
            "suggestion": " (from a player's suggestion)",
            "document": " (from the rules document)",
        }
        who = _escape(bot.name_of(current.guild_id, entry.by)[:40])
        lines.append(
            f"• Version {entry.version}, by {who}, <t:{entry.at}:d>{how.get(entry.source, '')}"
        )
    if len(log) > HISTORY_SHOWN:
        lines.append(f"…and {len(log) - HISTORY_SHOWN} older.")
    await _tell(interaction, "\n".join(lines))


class UndoPublish(_Menu):
    """Puts the rules from before a publish back as the next version, so followers that took
    the mistake get the correction as a normal update. It doesn't delete the version: the
    history stays complete."""

    def __init__(self, campaign: Campaign, version: int) -> None:
        super().__init__()
        self.campaign, self.version = campaign, version
        self.add_item(_Button(self._undo, label=UNDO_LABEL))

    @_guarded
    async def _undo(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        refused = await _bot(interaction).plan_gate(
            "share", current.guild_id, current, interaction.user.id
        )
        if refused is not None:
            await _tell(interaction, refused)
            return
        before = await store.content_at(current.guild_id, current.id, self.version - 1)
        if before is None:
            await _tell(interaction, "There is no earlier version to go back to.")
            return
        self.stop()
        made = await store.save_version(
            current.guild_id,
            current.id,
            interaction.user.id,
            before,
            plan_ok=True,  # checked just above with plan_gate
            expected_version=self.version,  # only while that publish is still the newest
        )
        await _tell(
            interaction,
            f"↩️ Undone. The rules from before are now version {made.version}. Tables that took "
            f"version {self.version} get this one at their next session start.",
        )
