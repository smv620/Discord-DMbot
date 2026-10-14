"""The publisher's side of shared house rules (#1191): publishing a new version, and who can
edit the set. Reached from the Shared house rules menu in ⚙️ Settings; every press re-checks
that the person is a DM of the campaign. Followers get a new version at their next session
start, never sooner, and may take it or pick from it (dmbot.dm_screen.set_update)."""

from __future__ import annotations

from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.rules import rule_sets, set_diff
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _Select, _send, _tell
from dmbot.ui.shared_rules import (
    CANCEL_LABEL,
    _escape,
    _guarded,
    _ready,
    _rules,
)

SHOWN_CHANGES = 12  # the lines of a diff quoted on the screen; the rest are counted
BODY_MAX = 1400  # the diff's characters: the whole message stays under Discord's 2000
NAMES_SHOWN = 10  # co-editors named in the history text
EDITORS_MAX = 25  # what the remove menu can hold
YOUR_RULES_CHANGED = (
    "Your house rules changed while you were looking. Press 🔄 Publish my changes again to "
    "see the new changes."
)
NOT_SHARED_HERE = "Your rules aren't shared yet. Use 📤 Share my house rules first."
HISTORY_SHOWN = 8
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
    body = changes_text(changes) or "Only the rules document link changed."
    await _send(
        interaction,
        f"**{_escape(mine.name[:60])}**: publish as version {mine.version + 1}? "
        f"{set_diff.count_words(changes)} changed:\n{body}\n"
        "Tables using it get the new version at their next session start.",
        ConfirmPublish(current, mine.version, after),
    )


class ConfirmPublish(_Menu):
    def __init__(
        self, campaign: Campaign, version: int, shown: dict[str, Any] | None = None
    ) -> None:
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
        if self.shown is not None and rule_sets.content_of(rules, link) != self.shown:
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
        await _tell(
            interaction,
            f"✅ **{_escape(made.name[:60])}** is now version {made.version} "
            f"({_rules(len(rules))}). Tables get it at their next session start.",
        )

    async def _no(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _tell(interaction, "Okay, nothing changed.")


@_guarded
async def show_history(interaction: discord.Interaction, campaign: Campaign) -> None:
    """Who made each version and who else can edit the set."""
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
    editors = await store.editors(current.guild_id, current.id)
    if editors:
        shown = [_escape(bot.name_of(current.guild_id, e)[:40]) for e in editors[:NAMES_SHOWN]]
        extra = len(editors) - len(shown)
        more = f" and {extra} more" if extra > 0 else ""
        lines.append(f"Co-editors: {', '.join(shown)}{more}")
    else:
        lines.append("No co-editors. Any DM of this campaign can change the set.")
    by_id = {e: bot.name_of(current.guild_id, e) for e in editors[:EDITORS_MAX]}
    await _send(interaction, "\n".join(lines), EditorsMenu(current, editors, by_id))


class _DmSelect(discord.ui.UserSelect[Any]):
    def __init__(self, on_pick: Any) -> None:
        super().__init__(placeholder="Add a co-editor…", row=0)
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.on_pick(interaction, self.values[0])


class EditorsMenu(_Menu):
    def __init__(
        self, campaign: Campaign, editors: list[int], names: dict[int, str] | None = None
    ) -> None:
        super().__init__()
        self.campaign = campaign
        names = names or {}
        self.add_item(_DmSelect(self._add))
        if editors:
            self.add_item(
                _Select(
                    self._remove,
                    placeholder="Remove a co-editor…",
                    options=[
                        discord.SelectOption(label=(names.get(e) or str(e))[:25], value=str(e))
                        for e in editors[:EDITORS_MAX]
                    ],
                    row=1,
                )
            )

    @_guarded
    async def _add(self, interaction: discord.Interaction, person: Any) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        if getattr(person, "bot", False):
            await _tell(interaction, "Bots can't edit rules.")
            return
        if len(await store.editors(current.guild_id, current.id)) >= EDITORS_MAX:
            await _tell(
                interaction,
                f"That's the most co-editors a set can have ({EDITORS_MAX}). Remove one first.",
            )
            return
        await store.add_editor(current.guild_id, current.id, interaction.user.id, person.id)
        name = _escape(_bot(interaction).name_of(current.guild_id, person.id)[:40])
        await _send(
            interaction,
            f"✅ **{name}** is a co-editor now and can change the set.",
            UndoEditor(current, person.id, added=True),
        )

    @_guarded
    async def _remove(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        editor_id = int(select.values[0])
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        removed = await store.remove_editor(
            current.guild_id, current.id, interaction.user.id, editor_id
        )
        name = _escape(_bot(interaction).name_of(current.guild_id, editor_id)[:40])
        if not removed:
            await _tell(interaction, f"**{name}** wasn't a co-editor.")
            return
        await _send(
            interaction,
            f"**{name}** is no longer a co-editor.",
            UndoEditor(current, editor_id, added=False),
        )


class UndoEditor(_Menu):
    def __init__(self, campaign: Campaign, editor_id: int, *, added: bool) -> None:
        super().__init__()
        self.campaign, self.editor_id, self.added = campaign, editor_id, added
        self.add_item(_Button(self._undo, label="↩️ Undo"))

    @_guarded
    async def _undo(self, interaction: discord.Interaction) -> None:
        ready = await _ready(interaction, self.campaign)
        if ready is None:
            return
        current, store = ready
        if self.added:
            await store.remove_editor(
                current.guild_id, current.id, interaction.user.id, self.editor_id
            )
        else:
            await store.add_editor(
                current.guild_id, current.id, interaction.user.id, self.editor_id
            )
        self.stop()
        name = _escape(_bot(interaction).name_of(current.guild_id, self.editor_id)[:40])
        done = "is not a co-editor" if self.added else "is a co-editor again"
        await _tell(interaction, f"↩️ Undone: {name} {done}.")
