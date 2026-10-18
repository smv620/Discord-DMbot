"""A followed house-rules set changed (#1191): what happens at a session's start, and the note.

At the start of a session (never during one, so a table's rules don't change mid-game) DMbot
asks whether the set the campaign follows has a newer version. If it does, DMbot **takes it
straight away** (Follow is what happens when the DM does nothing, and the take is credited to
DMbot, not to a DM) and posts one note on the DM screen: "<set> house rules updated to v5:
[See changes] [Pick changes] [Undo]".

- **Undo** goes back to the copy the campaign had. It is kept in the follow's own row, so the
  button works after a restart. DMbot does not take that same version again at the next
  session start, only a newer one (`declined_version`); See changes and Pick changes on the
  same note still work, and Pick needs no second undo.
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
from collections.abc import Sequence
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.dm_screen import set_suggestion, shared_update
from dmbot.rules import rule_sets, set_diff, set_sync
from dmbot.rules.house import HouseRule, HouseRuleError
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _Select, _tell

log = logging.getLogger(__name__)

PAGE = 25  # a Discord menu holds 25 choices
TEXT_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds around it
MORE_ROOM = 80  # "…and N more. Use Pick changes to go through all of them." and a line break
AND_MORE = "\n…and more."
ONLY_DMS = "Only this campaign's DMs can do that."
GONE = "That campaign isn't here any more."
NEWER = (
    "A newer version came after that note. Look for the newest note on the DM screen. "
    "Nothing changed."
)
NOT_FOLLOWING_NOW = "This campaign doesn't follow that set any more. Nothing changed."
UNDONE_AND_WAITING = "DMbot will take the next new version; use Pick changes to choose."
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
UNDO_LABEL = "↩️ Undo"


LINK_ONLY = "only the rules document link changed"


OVERRIDDEN = (
    "The set changed a rule your table has its own version of. Yours still applies. "
    "Press Compare to see both."
)


# A combined note may have several Compare buttons, so the line says which ("{n}" is its number).
OVERRIDDEN_HERE = "Your own version still applies. Press Compare {n} to see both."


async def settle(bot: Any, campaign: Campaign) -> None:
    """The relationship definitions borrowed from the followed set match it again (#1456). A
    failure never undoes what the DM or DMbot just did; the next session start tries again."""
    memory, sets = getattr(bot, "memory", None), getattr(bot, "rule_sets", None)
    if memory is not None and sets is not None:
        await set_sync.safely(set_sync.reconcile(memory, sets, campaign.guild_id, campaign.id))


def overrides(
    own: Sequence[HouseRule], changes: Sequence[set_diff.Change]
) -> list[tuple[set_diff.Change, HouseRule]]:
    """The updated (or removed) shared rules that one of the campaign's OWN rules overrides: its
    "instead of" is the shared rule's old words. Rules merged in from a set are not its own."""
    found = []
    for change in changes:
        if change.kind == "added" or change.before is None:
            continue
        for rule in own:
            if not rule.from_set and rule_sets.same_words(rule.supersedes, change.before["rule"]):
                found.append((change, rule))
                break
    return found


def compare_text(name: str, pairs: Sequence[tuple[set_diff.Change, HouseRule]]) -> str:
    """For each overridden shared rule: the old shared words, the new ones, and the campaign's."""
    md = discord.utils.escape_markdown
    blocks = []
    for change, mine in pairs:
        before = change.before["rule"] if change.before else ""
        now = change.after["rule"] if change.after else "(removed from the set)"
        blocks.append(
            f"Shared rule {change.number}\nBefore: {md(before)}\nNow: {md(now)}\n"
            f"Yours (still applies): {md(mine.rule)}"
        )
    head = f"**{md(name)}**: {OVERRIDDEN}\n\n"
    text = "\n\n".join(blocks)
    room = TEXT_MAX - len(head)  # the heading is part of the one message, and not a short one
    if len(text) > room:
        text = fit_message(text, limit=room - len(AND_MORE))[:-1].rstrip() + AND_MORE
    return head + text


def rules_phrase(changes: int, link_changed: bool = False, relationships: int = 0) -> str:
    rules = f"{changes} rule" + ("" if changes == 1 else "s")
    if relationships:
        more = f"{relationships} relationship" + ("" if relationships == 1 else "s")
        rules = more if changes == 0 else f"{rules} and {more}"
    if link_changed:
        rules = (
            LINK_ONLY
            if changes == 0 and not relationships
            else f"{rules} and the rules document link"
        )
    return rules


def note_text(
    follow_name: str,
    version: int,
    changes: int,
    link_changed: bool = False,
    overridden: bool = False,
    relationships: int = 0,
) -> str:
    rules = rules_phrase(changes, link_changed, relationships)
    text = (
        f"📚 **{discord.utils.escape_markdown(follow_name)}** house rules updated to "
        f"v{version}: {rules}. DMbot took the new version."
    )
    return f"{text}\n{OVERRIDDEN}" if overridden else text


def note_view(
    campaign_id: str, version: int, compare: bool = False, *, row: int | None = None, tag: str = ""
) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for item in note_buttons(campaign_id, version, compare, row, tag):
        view.add_item(item)
    return view


def note_buttons(
    campaign_id: str, version: int, compare: bool, row: int | None, tag: str
) -> list[discord.ui.Item[Any]]:
    """The buttons of one update. In a combined note each row carries its line's number (`tag`),
    so "Undo 2" is plainly the second line's."""
    actions = ("see", "pick") + (("compare",) if compare else ()) + ("undo",)
    return [SetUpdateButton(campaign_id, a, version, row=row, tag=tag) for a in actions]


_LABELS = {
    "see": ("See changes", discord.ButtonStyle.secondary),
    "pick": ("Pick changes", discord.ButtonStyle.secondary),
    "undo": ("Undo", discord.ButtonStyle.secondary),
    "compare": ("Compare", discord.ButtonStyle.secondary),
}


class SetUpdateButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:setupd:(?P<action>see|pick|undo|compare):(?P<campaign>[0-9a-f]{32}):(?P<version>[0-9]{1,9})",
):
    def __init__(
        self, campaign_id: str, action: str, version: int, *, row: int | None = None, tag: str = ""
    ) -> None:
        label, style = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=shared_update.numbered(label, tag),
                style=style,
                custom_id=f"dmbot:setupd:{action}:{campaign_id}:{version}",
                row=row,
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
        await _tell(interaction, NOT_FOLLOWING_NOW)
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
    if follow.version > version:  # the note is for an older version than the campaign has
        await _tell(interaction, NEWER)
        return
    if follow.version < version:
        # The note's update was undone and the campaign is back on the earlier copy. The note
        # is still true while that version is the newest: See changes and Pick changes work
        # on it as they did (Pick needs no second undo), and Undo has nothing left to do.
        update = await sets.pending_update(campaign.guild_id, campaign.id)
        if update is None or update.offer.version != version:
            await _tell(interaction, NEWER)
            return
        if action == "undo":
            await _tell(
                interaction,
                f"Already undone. The campaign is on v{follow.version}. {UNDONE_AND_WAITING}",
            )
        elif action == "see":
            await _tell(
                interaction,
                changes_text(
                    follow.name,
                    version,
                    list(update.changes),
                    update.link_changed,
                    update.def_changes,
                ),
            )
        elif action == "compare":
            await _compare(interaction, campaign, follow.name, list(update.changes))
        else:
            await _open_picker(interaction, campaign, update)
        return
    if action == "compare":
        before = follow.previous_content
        if before is None:
            await _tell(interaction, "There's nothing to compare: that update was undone.")
            return
        await _compare(interaction, campaign, follow.name, set_diff.diff(before, follow.content))
        return
    if action == "see":
        before = follow.previous_content
        if before is None:
            await _tell(interaction, "There's nothing to compare: that update was undone.")
            return
        await _tell(
            interaction,
            changes_text(
                follow.name,
                version,
                set_diff.diff(before, follow.content),
                before.get("link") != follow.content.get("link"),
                set_diff.diff_definitions(before, follow.content),
            ),
        )
        return
    try:
        await sets.undo_last_take(campaign.guild_id, campaign.id, interaction.user.id, version)
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    await settle(_bot(interaction), campaign)
    if action == "undo":
        back = follow.previous_version
        await _tell(interaction, f"↩️ Undone. Back on v{back}. {UNDONE_AND_WAITING}")
        return
    update = await sets.pending_update(campaign.guild_id, campaign.id)
    if update is None or update.offer.version != version:
        await _tell(interaction, NEWER)  # undone, and a different version is waiting now
        return
    await _open_picker(interaction, campaign, update)


async def _open_picker(
    interaction: discord.Interaction, campaign: Campaign, update: rule_sets.Update
) -> None:
    view = PickView(campaign, update)
    await _tell(interaction, view.text(), view=view)
    view.origin = interaction


def changes_text(
    name: str,
    version: int,
    changes: list[set_diff.Change],
    link_changed: bool = False,
    def_changes: Sequence[set_diff.DefChange] = (),
) -> str:
    lines = [set_diff.describe(c) for c in changes]
    lines += [set_diff.describe_definition(c) for c in def_changes]
    if link_changed:
        lines.append("The rules document link changed.")
    text = "\n".join(lines) or "Only the version number changed. No rule is different."
    if len(text) > TEXT_MAX:
        # Room for the line below, so the whole text still fits one message.
        kept = fit_message(text, limit=TEXT_MAX - MORE_ROOM)[:-1].rstrip()
        left = len(lines) - kept.count("\n") - 1
        text = kept + f"\n…and {left} more. Use Pick changes to go through all of them."
    return f"**{discord.utils.escape_markdown(name)}** v{version}:\n{text}"


async def _own_rules(bot: Any, campaign: Campaign) -> list[HouseRule]:
    """The campaign's house rules, or none if they can't be read (a note is worth posting
    without its Compare line)."""
    try:
        store = bot.house_rules
        if store is None:
            return []
        return list(await store.list(campaign.guild_id, campaign.id))
    except Exception:
        log.warning("Couldn't read house rules for a Compare line", exc_info=True)
        return []


async def _compare(
    interaction: discord.Interaction,
    campaign: Campaign,
    name: str,
    changes: list[set_diff.Change],
) -> None:
    """[Compare]: the old shared words, the new ones, and the campaign's own, for each shared
    rule that was updated and that the campaign overrides."""
    pairs = overrides(await _own_rules(_bot(interaction), campaign), changes)
    if not pairs:
        await _tell(interaction, "None of your own rules is about a rule that changed.")
        return
    await _tell(interaction, compare_text(name, pairs))


async def take(
    interaction: discord.Interaction,
    campaign: Campaign,
    update: rule_sets.Update,
    accepted: set[int] | None,
    accepted_defs: set[str] | None = None,
) -> None:
    bot = _bot(interaction)
    sets = bot.rule_sets
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
            accepted_defs=accepted_defs,
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    name = discord.utils.escape_markdown(update.offer.name)
    changed = {c.number for c in update.changes}
    left_out = len(changed - (accepted or changed))
    def_batch: int | None = None
    if taken.merged:
        # The follow ended: the relationships the DM kept become the campaign's own, the rest
        # of the set's go, in one memory change that Undo takes back.
        memory = getattr(bot, "memory", None)
        if memory is not None:
            done = await set_sync.safely(
                set_sync.keep_as_own(memory, campaign.guild_id, campaign.id, taken)
            )
            def_batch = done.batch if done is not None else None
        kept_rules = len(taken.added)
        mine = f"{kept_rules} rule" + ("" if kept_rules == 1 else "s")
        if taken.kept_definitions:
            n = len(taken.kept_definitions)
            mine += f" and {n} relationship" + ("" if n == 1 else "s")
        out = f"left out {left_out} change" + ("" if left_out == 1 else "s")
        words = (
            f"✅ Done. **{name}** is now your own copy: you kept {mine} and {out}. "
            "You no longer get its updates. Your own rules still win."
        )
    else:
        await settle(bot, campaign)
        words = f"✅ Now using **{name}** v{taken.after_version}."
    await _tell(interaction, words, view=UndoView(campaign, taken, def_batch))


class UndoView(_Menu):
    def __init__(
        self, campaign: Campaign, taken: rule_sets.Taken, def_batch: int | None = None
    ) -> None:
        super().__init__()
        self.campaign, self.taken = campaign, taken
        self.def_batch = def_batch  # the memory change Cherry pick made for the relationships
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
        memory = getattr(bot, "memory", None)
        if self.taken.merged and self.def_batch is not None and memory is not None:
            # The relationships go back as the set's (or are made again) before the follow is
            # reconciled, so Undo leaves nothing half done.
            await set_sync.safely(memory.undo(current.guild_id, current.id, self.def_batch))
        await settle(bot, current)
        v = self.taken.before.version
        if self.taken.merged:
            edited = (
                f" {kept} merged rule{'s' if kept != 1 else ''} you edited stayed." if kept else ""
            )
            await _tell(
                interaction,
                f"↩️ Undone. Back on v{v} and following it again.{edited} {UNDONE_AND_WAITING}",
            )
        else:
            await _tell(interaction, f"↩️ Undone. Back on v{v}. {UNDONE_AND_WAITING}")


_KIND_WORD = {"added": "New", "changed": "Changed", "removed": "Removed"}


class PickView(_Menu):
    """Cherry pick: every change starts ticked (kept). Untick what the DM doesn't want, then
    Save my picks. Pages of 25, because a Discord menu holds no more. The set's relationship
    changes (#1456) are picked one at a time in the same list, after the rules."""

    def __init__(self, campaign: Campaign, update: rule_sets.Update) -> None:
        super().__init__()
        self.campaign, self.update = campaign, update
        # (value, short label, one line): "r:<rule number>" or "d:<relationship key>".
        self.items: list[tuple[str, str, str]] = [
            (f"r:{c.number}", f"{c.number} {c.kind.capitalize()}", set_diff.describe(c, 90))
            for c in update.changes
        ] + [
            (f"d:{c.key}", f"{_KIND_WORD[c.kind]}: {c.label}", set_diff.describe_definition(c))
            for c in update.def_changes
        ]
        self.accepted = {value for value, _label, _line in self.items}
        self.page = 0
        self._draw()

    def _page_items(self) -> list[tuple[str, str, str]]:
        return self.items[self.page * PAGE : (self.page + 1) * PAGE]

    def text(self) -> str:
        pages = max(1, -(-len(self.items) // PAGE))
        where = f" (page {self.page + 1} of {pages})" if pages > 1 else ""
        lines = "\n".join(line[:70] for _value, _label, line in self._page_items())
        link = "\nThe rules document link changed too." if self.update.link_changed else ""
        return (
            f"Pick changes: **{discord.utils.escape_markdown(self.update.offer.name)}**{where}. "
            "Ticked = keep. Everything starts ticked. If you untick any, you stop following "
            "the set and get your own copy; relationships you keep become yours. You can undo it.\n"
            + lines
            + link
        )

    def _draw(self) -> None:
        self.clear_items()
        shown = self._page_items()
        if shown:
            options = [
                discord.SelectOption(
                    label=label[:25],
                    description=line[:100],
                    value=value,
                    default=value in self.accepted,
                )
                for value, label, line in shown
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
        if len(self.items) > PAGE:
            self.add_item(_Button(self._prev, label="◀ Back", row=1, disabled=self.page == 0))
            last = (self.page + 1) * PAGE >= len(self.items)
            self.add_item(_Button(self._next, label="More ▶", row=1, disabled=last))
        self.add_item(
            _Button(self._apply, label="Save my picks", style=discord.ButtonStyle.primary, row=1)
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        select = next(c for c in self.children if isinstance(c, discord.ui.Select))
        chosen = set(select.values)
        shown = {value for value, _l, _line in self._page_items()}
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
        numbers = {int(v[2:]) for v in self.accepted if v.startswith("r:")}
        keys = {v[2:] for v in self.accepted if v.startswith("d:")}
        # The version the DM was shown, not whatever is newest now: if a newer one has come,
        # nothing is applied and the DM is told (the store compares the two).
        await take(interaction, self.campaign, self.update, numbers, keys)


async def check_update(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """This set's check on its own: collect, then post (session starts use `update_notes`)."""
    found = shared_update.Collected()
    await collect(bot, campaign, screen_id, found)
    try:
        await shared_update.post(bot, campaign.id, screen_id, found)
    except Exception:
        log.exception("Couldn't post a shared-rules update note")


async def collect(
    bot: Any, campaign: Campaign, screen_id: int, found: shared_update.Collected
) -> None:
    """At a session's start: take a newer version of the followed set at once and add it to
    `found` for the DM-screen note. Never raises; a set whose publisher stopped sharing is marked
    "no longer updated" and said once (as a line in `found`). The suggestion answers and notes
    are told here, on the screen, as they come."""
    sets = bot.rule_sets
    if sets is None:
        return
    # Suggestions first: answers to the ones this campaign sent, and notes for its own set that
    # never reached the screen. Each never raises.
    await set_suggestion.tell_answers(bot, campaign, screen_id)
    await set_suggestion.post_waiting(bot, campaign)
    # The borrowed relationships match the followed set (#1456), whatever happened since: a
    # restored backup, a step that failed halfway.
    await settle(bot, campaign)
    try:
        update = await sets.pending_update(campaign.guild_id, campaign.id, for_dmbot=True)
        if update is None:
            if await sets.is_gone(campaign.guild_id, campaign.id):
                # The house-rules follow is the one is_gone and stop_updating acted on.
                houses = [
                    f
                    for f in await sets.following(campaign.guild_id, campaign.id)
                    if f.kind == rule_sets.HOUSE_RULES and f.updating
                ]
                stopped = await sets.stop_updating(campaign.guild_id, campaign.id)
                if not stopped:
                    return  # another check got there first and said it: said once
                if houses:
                    name = discord.utils.escape_markdown(houses[0].name)
                    text = (
                        f"📚 **{name}** isn't being shared any more. "
                        f"Your table keeps version {houses[0].version}, and it won't get updates."
                    )
                else:
                    text = (
                        "📚 A set your table follows isn't being shared any more. "
                        "You keep your copy, and it won't get updates."
                    )
                found.gone.append(text)
            return
        await sets.take_update(  # DMbot takes it: Follow is the default when the DM does nothing
            campaign.guild_id,
            campaign.id,
            None,
            None,
            expected_version=update.offer.version,
        )
        await settle(bot, campaign)
        overridden = bool(overrides(await _own_rules(bot, campaign), update.changes))
        name, version = update.offer.name, update.offer.version
        rules = rules_phrase(len(update.changes), update.link_changed, len(update.def_changes))
        found.updates.append(
            shared_update.Update(
                line=(
                    f"**{discord.utils.escape_markdown(name)}** house rules, "
                    f"now v{version}: {rules}"
                ),
                single_text=note_text(
                    name,
                    version,
                    len(update.changes),
                    update.link_changed,
                    overridden,
                    len(update.def_changes),
                ),
                single_view=lambda: note_view(campaign.id, version, overridden),
                buttons=lambda row, tag: note_buttons(campaign.id, version, overridden, row, tag),
                extras=[OVERRIDDEN_HERE] if overridden else [],
            )
        )
    except Exception:
        log.exception("Couldn't check for a shared-rules update")
