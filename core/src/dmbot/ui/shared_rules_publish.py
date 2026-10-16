"""The publisher's side of shared house rules (#1191): publishing a new version, undoing it,
and the version history. Only the campaign's DMs edit the set (no separate co-editor list; owner
decision, PLAN #1257). Reached from the Shared house rules menu in ⚙️ Settings; every press
re-checks that the person is a DM of the campaign. Followers get a new version at their next session
start, never sooner, and may take it or pick from it (dmbot.dm_screen.set_update)."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import set_definitions as sd
from dmbot.memory.definition_content import MAX_DEFINITIONS
from dmbot.rules import rule_sets, set_diff
from dmbot.ui import shared_rules_defs as defs_ui
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
HOW = {  # how a version came about, when it wasn't a DM's own publish
    "suggestion": " (from a player's suggestion)",
    "document": " (from the rules document)",
}
LINK_ONLY = "Only the rules document link changed."
UNDO_LABEL = "↩️ Undo"
COULD_NOT_ADD = (
    "DMbot couldn't add every relationship you ticked. Press 🔄 Publish my changes to try again."
)
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


async def carried(
    interaction: discord.Interaction,
    current: Campaign,
    before: dict[str, Any],
    extra: Collection[str] = (),
) -> list[dict[str, Any]]:
    """The relationship definitions the next version carries (#1456): the ones the set carries
    now, each as the campaign has it today (a changed meaning travels; one the campaign no
    longer has, or can no longer share, drops out), then the `extra` ones the DM ticked to add
    (#1475). When the memory can't be read the set's definitions stay as they are, so a hiccup
    never takes them away."""
    held = list(set_diff.definitions_of(before).values())
    memory = getattr(_bot(interaction), "memory", None)
    if memory is None or not (held or extra):
        return held
    try:
        now = {d["key"]: d for d in await sd.own_definitions(memory, current.guild_id, current.id)}
    except Exception:
        return held
    kept = [now[d["key"]] for d in held if d["key"] in now]
    have = {d["key"] for d in held}
    return kept + [now[k] for k in sorted(extra) if k in now and k not in have]


async def new_offers(
    interaction: discord.Interaction, current: Campaign, before: dict[str, Any]
) -> list[dict[str, Any]]:
    """The campaign's own shareable relationships the set doesn't carry yet (#1475), as many as
    the set has room for. The same ones Share offers, minus what is already in the set."""
    have = set_diff.definitions_of(before)
    # Counts every definition the set holds, even one the campaign no longer has: a little room
    # may go unoffered, never a set over its limit.
    room = min(MAX_DEFINITIONS - len(have), defs_ui.MENU_MAX)
    if room <= 0:
        return []
    offered = await defs_ui.offered_for(_bot(interaction), current)
    return [d for d in offered if d["key"] not in have][:room]


async def _gate(
    interaction: discord.Interaction, campaign: Campaign
) -> tuple[Campaign, Any] | None:
    """The person is a DM of the campaign and its plan allows sharing, or they are told why not.
    Asked before the diff, so nobody reads changes they can't publish (as Share does), and again
    on a tick-list press, which may come minutes after the list was shown."""
    ready = await _ready(interaction, campaign)
    if ready is None:
        return None
    current, store = ready
    refused = await _bot(interaction).plan_gate(
        "share", current.guild_id, current, interaction.user.id
    )
    if refused is not None:
        await _tell(interaction, refused)
        return None
    return current, store


@_guarded
async def show_changes(interaction: discord.Interaction, campaign: Campaign) -> None:
    """What would change if the campaign's house rules became the set's next version."""
    ready = await _gate(interaction, campaign)
    if ready is None:
        return
    current, store = ready
    shared = await store.current_content(current.guild_id, current.id)
    if shared is None:
        await _tell(interaction, NOT_SHARED_HERE)
        return
    offers = await new_offers(interaction, current, shared[1])
    if not offers:
        await _show_diff(interaction, current, store, ())
        return

    @_guarded
    async def chosen(i: discord.Interaction, picked: list[dict[str, Any]]) -> None:
        again = await _gate(i, campaign)
        if again is not None:
            await _show_diff(i, again[0], again[1], [d["key"] for d in picked])

    await _send(
        interaction,
        defs_ui.tick_list_text(offers, defs_ui.NEW_TEXT),
        defs_ui.ShareChoose(
            offers,
            chosen,
            start_ticked=False,
            go_label="Add ticked",
            skip_label="Not now",
            placeholder="Tick the ones to add",
        ),
    )


async def _show_diff(
    interaction: discord.Interaction, current: Campaign, store: Any, add: Collection[str]
) -> None:
    """The changes a publish would make, with the relationships the DM ticked to add, and the
    button to publish them. Reads the set again: a press on the tick list may come minutes later."""
    shared = await store.current_content(current.guild_id, current.id)  # again: see above
    if shared is None:
        await _tell(interaction, NOT_SHARED_HERE)
        return
    mine, before = shared
    got = await _current_rules(interaction, current)
    if got is None:
        return
    rules, link = got
    after = rule_sets.content_of(rules, link, await carried(interaction, current, before, add))
    kept = {d["key"] for d in after["definitions"]}
    if any(k not in kept for k in add):
        # One ticked is gone or can't be read now (or the set is full): say so, never publish a
        # version without what the DM asked for.
        await _tell(interaction, COULD_NOT_ADD)
        return
    changes = set_diff.diff(before, after)
    def_changes = set_diff.diff_definitions(before, after)
    if not changes and not def_changes and before.get("link") == after.get("link"):
        await _tell(interaction, NOTHING_NEW)
        return
    body = changes_text(changes)
    if def_changes:
        extra = "\n".join(_escape(set_diff.describe_definition(c)) for c in def_changes[:6])
        body = f"{body}\n{extra}" if body else extra
    if before.get("link") != after.get("link"):
        body = f"{body}\nThe rules document link changed." if body else LINK_ONLY
    count = (
        f"{set_diff.count_words(changes, def_changes)} changed"
        if changes or def_changes
        else "no rule changed"
    )
    await _send(
        interaction,
        f"**{_escape(mine.name[:60])}**: publish as version {mine.version + 1}? "
        f"{count}:\n{body}\n"
        "Tables using it get the new version at their next session start.",
        ConfirmPublish(current, mine.version, after, tuple(add)),
    )


class ConfirmPublish(_Menu):
    def __init__(
        self, campaign: Campaign, version: int, shown: dict[str, Any], extra: Collection[str] = ()
    ) -> None:
        super().__init__()
        self.campaign, self.version, self.shown = campaign, version, shown
        self.extra = tuple(extra)  # the new relationships the DM ticked (#1475)
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
        again = await carried(interaction, current, shared[1], self.extra)
        if rule_sets.content_of(rules, link, again) != self.shown:
            # What would be published is not what the DM saw: they look again first.
            await _tell(interaction, YOUR_RULES_CHANGED)
            return
        made = await store.publish_changes(
            current.guild_id,
            current.id,
            interaction.user.id,
            rules,
            link,
            plan_ok=True,  # checked just above with plan_gate
            expected_version=self.version,  # and again under the lock, in the store
            definitions=self.shown.get("definitions", []),  # exactly what the DM was shown
        )
        self.stop()  # only after it worked: a refused press can be tried again
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
        who = _escape(bot.name_of(current.guild_id, entry.by)[:40])
        lines.append(
            f"• Version {entry.version}, by {who}, <t:{entry.at}:d>{HOW.get(entry.source, '')}"
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
        made = await store.save_version(
            current.guild_id,
            current.id,
            interaction.user.id,
            before,
            plan_ok=True,  # checked just above with plan_gate
            expected_version=self.version,  # only while that publish is still the newest
        )
        self.stop()
        await _tell(
            interaction,
            f"↩️ Undone. Version {made.version} has the rules from before. Tables get it at "
            "their next session start.",
        )
