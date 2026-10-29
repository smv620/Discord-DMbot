"""A followed homebrew pack changed (#1527 part C): what happens at a session's start, and the note.

The same as house-rules sets (`set_update`): at the start of a session (never during one)
DMbot asks whether a pack the campaign follows has a newer version. If it does, DMbot **takes it
straight away** (Follow is what happens when the DM does nothing; the take is credited to DMbot)
and posts one note on the DM screen:
"<pack> homebrew updated to v5: [See changes] [Pick changes] [Undo]".

- **Undo** goes back to the copy the campaign had (kept in the follow's own row, so it works after a
  restart). DMbot does not take that same version again, only a newer one.
- **Pick changes** undoes the take and opens the per-change picker. Everything starts ticked.
  Unticking anything copies the pack (as the campaign had it plus the accepted changes) into the
  campaign's **own homebrew** and ends the follow: one operation, one Undo. The campaign's own
  entries win over a pack's.
- **See changes** lists what differs from the copy before.

Every press checks again that the person is one of the campaign's DMs and that the button is for
the version the campaign is on. Buttons survive a restart (`bot.add_dynamic_items`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

import discord

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.dm_screen import pack_suggestion, shared_update
from dmbot.rules import pack_diff
from dmbot.rules.homebrew import name_key
from dmbot.rules.house import HouseRuleError
from dmbot.rules.pack_updates import PackTaken, PackUpdate, PackUpdateStore
from dmbot.ui.dmbot_commands import _answer_first, _bot, _Button, _Menu, _Select, _tell

log = logging.getLogger(__name__)

PAGE = 25  # a Discord menu holds 25 choices
TEXT_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds around it
MORE_ROOM = 80  # "…and N more. Use Pick changes to go through all of them." and a line break
ONLY_DMS = "Only this campaign's DMs can do that."
GONE = "That campaign isn't here any more."
NEWER = (
    "A newer version came after that note. Look for the newest note on the DM screen. "
    "Nothing changed."
)
NOT_FOLLOWING_NOW = "This campaign doesn't follow that pack any more. Nothing changed."
UNDONE_AND_WAITING = (
    "DMbot will take the next newer version at a session start. "
    "Press Pick changes on the note if you'd rather choose."
)
COULDNT = "That didn't work, and nothing changed. Press the button again in a moment."
UNDO_LABEL = "↩️ Undo"
YOURS_WIN = "Your own homebrew has the same name as {names}. Yours still applies."
WAITING = (
    "{entries} from {site} {verb} until you say you have the right to use that site's "
    "homebrew. Open ⚙️ Manage DMbot → Shared rules → Homebrew packs and press Use {site}."
)


def _md(text: str) -> str:
    return str(discord.utils.escape_markdown(text))


def _store(bot: Any) -> PackUpdateStore | None:
    sets = getattr(bot, "rule_sets", None)
    return sets if isinstance(sets, PackUpdateStore) else None


def _shown(names: Sequence[str]) -> str:
    shown = ", ".join(_md(n) for n in names[:3])
    return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")


def note_text(
    pack_name: str,
    version: int,
    changes: int,
    overridden: Sequence[str] = (),
    waiting: Mapping[str, int] | None = None,
) -> str:
    text = (
        f"📦 **{_md(pack_name)}** homebrew updated to v{version}: {entries_phrase(changes)}. "
        "DMbot took the new version."
    )
    extras = extra_lines(overridden, waiting)
    return "\n".join([text, *extras])


def entries_phrase(changes: int) -> str:
    return (
        "no entry is different"
        if changes == 0
        else f"{changes} entr" + ("y" if changes == 1 else "ies") + " changed"
    )


def extra_lines(overridden: Sequence[str], waiting: Mapping[str, int] | None = None) -> list[str]:
    """What the DM should know beyond the count: the campaign's own homebrew still wins, and
    entries from a site the campaign hasn't approved wait (#1635)."""
    lines = []
    for site, n in sorted((waiting or {}).items()):
        entries = "1 entry" if n == 1 else f"{n} entries"
        lines.append(WAITING.format(entries=entries, site=site, verb="waits" if n == 1 else "wait"))
    if overridden:
        lines.append(YOURS_WIN.format(names=_shown(overridden)))
    return lines


def gone_text(pack_name: str, version: int) -> str:
    return (
        f"📦 **{_md(pack_name)}** isn't being shared any more. You keep your copy (v{version}), "
        "but it won't get updates. Nothing to do."
    )


def note_view(campaign_id: str, set_id: str, version: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for item in note_buttons(campaign_id, set_id, version, None, ""):
        view.add_item(item)
    return view


def note_buttons(
    campaign_id: str, set_id: str, version: int, row: int | None, tag: str
) -> list[discord.ui.Item[Any]]:
    """The buttons of one update; in a combined note `tag` is the number of its line."""
    return [
        PackUpdateButton(campaign_id, set_id, action, version, row=row, tag=tag)
        for action in ("see", "pick", "undo")
    ]


_LABELS = {"see": "See changes", "pick": "Pick changes", "undo": "Undo"}


class PackUpdateButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:packupd:(?P<action>see|pick|undo):(?P<campaign>[0-9a-f]{32}):(?P<pack>[0-9a-f]{32}):(?P<version>[0-9]{1,9})",
):
    def __init__(
        self,
        campaign_id: str,
        set_id: str,
        action: str,
        version: int,
        *,
        row: int | None = None,
        tag: str = "",
    ) -> None:
        label = _LABELS[action]
        super().__init__(
            discord.ui.Button(
                label=shared_update.numbered(label, tag),
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:packupd:{action}:{campaign_id}:{set_id}:{version}",
                row=row,
            )
        )
        self.campaign_id, self.set_id, self.action, self.version = (
            campaign_id,
            set_id,
            action,
            version,
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> PackUpdateButton:
        return cls(match["campaign"], match["pack"], match["action"], int(match["version"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        try:
            await press(interaction, self.campaign_id, self.set_id, self.action, self.version)
        except Exception:
            log.exception("A homebrew pack update button failed")
            await _tell(interaction, COULDNT)


async def _dm_and_follow(
    interaction: discord.Interaction, campaign_id: str, set_id: str
) -> tuple[Campaign, Any] | None:
    """The campaign and its followed pack, if this person is one of its DMs now."""
    bot = _bot(interaction)
    sets = _store(bot)
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
    follow = next(
        (
            f
            for f in await sets.followed_packs(campaign.guild_id, campaign.id)
            if f.set_id == set_id
        ),
        None,
    )
    if follow is None:
        await _tell(interaction, NOT_FOLLOWING_NOW)
        return None
    return campaign, follow


def _changed(bot: Any, campaign: Campaign) -> None:
    """A running session reads the campaign's homebrew names again."""
    changed = getattr(bot, "homebrew_changed", None)
    if changed is not None:
        changed(campaign.guild_id, campaign.id)


def changes_text(name: str, version: int, changes: Sequence[pack_diff.Change]) -> str:
    lines = [pack_diff.describe(c) for c in changes]
    text = "\n".join(lines) or "Only the version number changed. No entry is different."
    if len(text) > TEXT_MAX:
        # Room for the line below, so the whole text still fits one message.
        kept = fit_message(text, limit=TEXT_MAX - MORE_ROOM)[:-1].rstrip()
        left = len(lines) - kept.count("\n") - 1
        text = kept + f"\n…and {left} more. Use Pick changes to go through all of them."
    return f"**{_md(name)}** v{version}:\n{text}"


async def press(
    interaction: discord.Interaction, campaign_id: str, set_id: str, action: str, version: int
) -> None:
    await _answer_first(interaction)
    found = await _dm_and_follow(interaction, campaign_id, set_id)
    if found is None:
        return
    campaign, follow = found
    sets = _store(_bot(interaction))
    assert sets is not None  # checked above
    if follow.version > version:  # the note is for an older version than the campaign has
        await _tell(interaction, NEWER)
        return
    if follow.version < version:
        # The note's update was undone and the campaign is back on the earlier copy. The note is
        # still true while that version is the newest: See and Pick work on it as they did.
        update = await sets.pending_pack_update(campaign.guild_id, campaign.id, set_id)
        if update is None or update.offer.version != version:
            await _tell(interaction, NEWER)
            return
        if action == "undo":
            await _tell(
                interaction,
                f"Already undone. The campaign is on v{follow.version}. {UNDONE_AND_WAITING}",
            )
        elif action == "see":
            await _tell(interaction, changes_text(follow.name, version, update.changes))
        else:
            await _open_picker(interaction, campaign, update)
        return
    if action == "see":
        before = follow.previous_content
        if before is None:
            await _tell(
                interaction,
                "There's nothing to compare: that update was undone. "
                "Look for the newest note on the DM screen.",
            )
            return
        await _tell(
            interaction,
            changes_text(follow.name, version, pack_diff.diff(before, follow.content)),
        )
        return
    try:
        await sets.undo_last_pack_take(
            campaign.guild_id, campaign.id, interaction.user.id, set_id, version
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    _changed(_bot(interaction), campaign)
    if action == "undo":
        await _tell(
            interaction, f"↩️ Undone. Back on v{follow.previous_version}. {UNDONE_AND_WAITING}"
        )
        return
    update = await sets.pending_pack_update(campaign.guild_id, campaign.id, set_id)
    if update is None or update.offer.version != version:
        await _tell(interaction, NEWER)  # undone, and a different version is waiting now
        return
    await _open_picker(interaction, campaign, update)


async def _open_picker(
    interaction: discord.Interaction, campaign: Campaign, update: PackUpdate
) -> None:
    view = PickView(campaign, update)
    await _tell(interaction, view.text(), view=view)
    view.origin = interaction


async def take(
    interaction: discord.Interaction,
    campaign: Campaign,
    update: PackUpdate,
    accepted: set[str] | None,
) -> None:
    bot = _bot(interaction)
    sets = _store(bot)
    if sets is None:
        await _tell(interaction, COULDNT)
        return
    try:
        taken = await sets.take_pack_update(
            campaign.guild_id,
            campaign.id,
            interaction.user.id,
            update.follow.set_id,
            accepted,
            expected_version=update.offer.version,  # the version the DM was shown
        )
    except HouseRuleError as exc:
        await _tell(interaction, str(exc))
        return
    _changed(bot, campaign)
    name = _md(update.offer.name)
    if taken.merged:
        left_out = len({c.key for c in update.changes} - (accepted or set()))
        copied = len(taken.added)
        already = f" ({taken.skipped} already yours)" if taken.skipped else ""
        words = (
            f"✅ Done. **{name}** is now your own homebrew. Copied {copied} "
            f"entr{'y' if copied == 1 else 'ies'}{already}, left out {left_out} "
            f"change{'' if left_out == 1 else 's'}. It won't update any more. "
            "Press Undo if you change your mind."
        )
    else:
        words = f"✅ Now using **{name}** v{taken.after_version}."
    await _tell(interaction, words, view=UndoView(campaign, taken))


class UndoView(_Menu):
    def __init__(self, campaign: Campaign, taken: PackTaken) -> None:
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
        sets = _store(bot)
        if sets is None:
            await _tell(interaction, COULDNT)
            return
        try:
            kept = await sets.undo_pack_take(
                current.guild_id, current.id, interaction.user.id, self.taken
            )
        except HouseRuleError as exc:
            await _tell(interaction, str(exc))
            return
        except Exception:
            log.exception("Couldn't undo a homebrew pack update")
            await _tell(interaction, COULDNT)
            return
        self.stop()
        _changed(bot, current)
        v = self.taken.before.version
        if self.taken.merged:
            edited = (
                f" {kept} entr{'ies' if kept != 1 else 'y'} you edited kept your edits."
                if kept
                else ""
            )
            await _tell(
                interaction,
                f"↩️ Undone. Back on v{v} and following it again.{edited} {UNDONE_AND_WAITING}",
            )
        else:
            await _tell(interaction, f"↩️ Undone. Back on v{v}. {UNDONE_AND_WAITING}")


class PickView(_Menu):
    """Pick changes: every change starts ticked (kept). Untick what the DM doesn't want, then Save
    my picks. Pages of 25, because a Discord menu holds no more."""

    def __init__(self, campaign: Campaign, update: PackUpdate) -> None:
        super().__init__()
        self.campaign, self.update = campaign, update
        self.items: list[tuple[str, str, str]] = [
            (c.key, c.name, pack_diff.describe(c, 90)) for c in update.changes
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
        return (
            f"Pick changes: **{_md(self.update.offer.name)}**{where}. Ticked = take this change. "
            "All start ticked, so leaving them keeps you on the pack. Untick any and this becomes "
            "your own homebrew: no more updates from the pack. Undo is on the next screen.\n"
            + lines
        )

    def _draw(self) -> None:
        self.clear_items()
        shown = self._page_items()
        if shown:
            options = [
                discord.SelectOption(
                    label=label[:100],
                    description=line[:100],
                    value=value,
                    default=value in self.accepted,
                )
                for value, label, line in shown
            ]
            self.pick = _Select(
                self._picked,
                placeholder="Changes to take",
                min_values=0,
                max_values=len(options),
                options=options,
                row=0,
            )
            self.add_item(self.pick)
        if len(self.items) > PAGE:
            self.add_item(_Button(self._prev, label="◀ Back", row=1, disabled=self.page == 0))
            last = (self.page + 1) * PAGE >= len(self.items)
            self.add_item(_Button(self._next, label="More ▶", row=1, disabled=last))
        self.add_item(
            _Button(self._apply, label="Save my picks", style=discord.ButtonStyle.primary, row=1)
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        chosen = set(self.pick.values)
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
        found = await _dm_and_follow(interaction, self.campaign.id, self.update.follow.set_id)
        if found is None:
            return
        self.stop()
        try:
            await take(interaction, self.campaign, self.update, set(self.accepted))
        except Exception:
            log.exception("Couldn't save the picks for a homebrew pack update")
            await _tell(interaction, COULDNT)


async def _waiting(bot: Any, campaign: Campaign) -> dict[str, int]:
    """Entries of the followed packs that wait for a site's approval, by site (#1635)."""
    reader = getattr(bot, "homebrew_reader", None)
    return await reader.waiting(campaign.guild_id, campaign.id) if reader is not None else {}


async def _overridden(bot: Any, campaign: Campaign, update: PackUpdate) -> list[str]:
    """The names of changed entries that the campaign's own homebrew also has (a note is worth
    posting without this line if the homebrew can't be read)."""
    try:
        store = bot.homebrew
        if store is None:
            return []
        mine = {name_key(e.name) for e in await store.list(campaign.guild_id, campaign.id)}
    except Exception:
        log.warning("Couldn't read homebrew for a pack update note", exc_info=True)
        return []
    return [c.name for c in update.changes if c.key in mine]


async def check_updates(bot: Any, campaign: Campaign, screen_id: int) -> None:
    """The packs' check on its own: collect, then post (session starts use `update_notes`)."""
    found = shared_update.Collected()
    await collect(bot, campaign, screen_id, found)
    try:
        await shared_update.post(bot, campaign.id, screen_id, found)
    except Exception:
        log.exception("Couldn't post a homebrew pack update note")


async def collect(
    bot: Any, campaign: Campaign, screen_id: int, found: shared_update.Collected
) -> None:
    """At a session's start: take a newer version of each followed pack at once and add it to
    `found` for the DM-screen note; a pack whose publisher stopped sharing is marked "no longer
    updated" and said once (a line in `found`). Never raises."""
    sets = _store(bot)
    if sets is None:
        return
    # Suggestions first, as for sets: the answers to the ones this campaign sent, and notes for
    # its own pack that never reached the screen (#1537). Each never raises.
    await pack_suggestion.tell_answers(bot, campaign, screen_id)
    await pack_suggestion.post_waiting(bot, campaign)
    try:
        for update in await sets.pending_pack_updates(
            campaign.guild_id, campaign.id, for_dmbot=True
        ):
            try:
                await sets.take_pack_update(  # Follow is the default when the DM does nothing
                    campaign.guild_id,
                    campaign.id,
                    None,
                    update.follow.set_id,
                    None,
                    expected_version=update.offer.version,
                )
            except HouseRuleError:
                continue  # someone got there first, or nothing is new
            _changed(bot, campaign)
            found.updates.append(await _update_note(bot, campaign, update))
        for gone in await sets.gone_packs(campaign.guild_id, campaign.id):
            if await sets.stop_updating_pack(campaign.guild_id, campaign.id, gone.set_id):
                found.gone.append(gone_text(gone.name, gone.version))
    except Exception:
        log.exception("Couldn't check for a homebrew pack update")


async def _update_note(bot: Any, campaign: Campaign, update: PackUpdate) -> shared_update.Update:
    """The note for a pack just taken. If its extra lines can't be worked out, the update still gets
    its buttons: the take already happened, so the DM must have an Undo."""
    name, version, set_id = update.offer.name, update.offer.version, update.follow.set_id
    try:
        overridden = await _overridden(bot, campaign, update)
        waiting = await _waiting(bot, campaign)
    except Exception:
        log.exception("Couldn't work out the extra lines of a pack update note")
        overridden, waiting = [], {}
    return shared_update.Update(
        line=f"**{_md(name)}** homebrew, now v{version}: {entries_phrase(len(update.changes))}",
        single_text=note_text(name, version, len(update.changes), overridden, waiting),
        single_view=lambda: note_view(campaign.id, set_id, version),
        buttons=lambda row, tag: note_buttons(campaign.id, set_id, version, row, tag),
        extras=extra_lines(overridden, waiting),
    )
