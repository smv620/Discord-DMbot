"""Find a name and its card (docs/PLAN.md, "Names at scale", step 2; #126).

🔍 Find a name (a one-field form, or `/dmbot names find:` with Discord's type-ahead)
opens a **name card**: what it is, when it was last heard, its other names, its secret
names (the campaign's DMs only) and its connections, with ✏️ Fix spelling, Change what it
is and 🗑 Remove this name (asks first; Undo) first, then Also called…, Edit other
names (⭐ main name, 🤫 secret, ✖ not this name), 🔗 Same as…, 🧭 Connect to… and Show
all. Everything answers from the in-memory copy of the names, refreshed right after
DMbot's own changes. Only the campaign's DMs and server managers, privately; every press
checks again.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    CONFIRMED,
    DM,
    MERGED,
    REJECTED,
    Alias,
    MemoryRuleError,
    Relation,
    TooLateToUndo,
    days,
    is_id,
    name_key,
)
from dmbot.memory.search import MAX_RESULTS, Match, find, said_lately
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _bot,
    _Button,
    _failed,
    _is_manager,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
)
from dmbot.ui.names import (
    GONE,
    KIND_SHORT,
    KINDS,
    NOT_AVAILABLE,
    PC,
    _campaign_for,
    _md,
    _memory,
    _UserSelect,
    already_known,
    changed,
    known_as,
    sees_secrets,
    split_names,
)

log = logging.getLogger(__name__)
ALSO_CALLED = "🏷️ Also called…"  # adds another name; its own icon, not Add a name's (#393)
NOTHING_CHANGED = (
    "Something went wrong and nothing was changed. Press 🔍 Find a name to open it and try again."
)

CARD_MAX = 1900  # under Discord's 2,000 characters
SHOWN = 3  # per section, then "… and N more"
NOTE_MAX = 600  # what just changed, above the card
NAME_LIMIT = 100  # what a form field may hold

# Connections in plain words (the fixed core of the memory rules).
CONNECTION_WORDS = {
    "located_in": "is in",
    "member_of": "is a member of",
    "ally_of": "is a friend or ally of",
    "enemy_of": "is an enemy of",
    "kin_of": "is family of",
    "owns": "owns",
    "knows": "knows",
    "serves": "works for",
}
# The same connection read from the other name's card ("Frostwolf tribe has as members
# Ulfgar"). Connections that read the same both ways use CONNECTION_WORDS.
CONNECTION_BACK = {
    "located_in": "is where you find",
    "member_of": "has as a member",
    "owns": "is owned by",
    "knows": "is known to",
    "serves": "is the boss of",
}
# On a card, where several names may follow ("members include Ulfgar, Kesh").
CARD_BACK = {**CONNECTION_BACK, "member_of": "members include"}
BOTH_WAYS = frozenset({"ally_of", "enemy_of", "kin_of"})
BACK = ":back"  # a "how" picked from the other name's side ("has as members…")


def connection_words(predicate: str) -> str:
    return CONNECTION_WORDS.get(predicate, predicate.replace("_", " "))


def sentence(lookup: CampaignLookup, r: Relation) -> str | None:
    """ "Ulfgar is a member of Frostwolf tribe", or None if either end is gone."""
    subject, obj = lookup.entities.get(r.subject_id), lookup.entities.get(r.object_id)
    if subject is None or obj is None:
        return None
    return f"{subject.name} {connection_words(r.predicate)} {obj.name}"


def connect_choices(name: str) -> list[tuple[str, str, str | None]]:
    """(value, label, description) for "How is it connected?": each connection from this
    name's side, each followed by the same one the other way round."""
    out: list[tuple[str, str, str | None]] = []
    for p, w in CONNECTION_WORDS.items():
        out.append((p, logic.shorten(f"{name} {w}…", logic.OPTION_LABEL_MAX), None))
        if p in CONNECTION_BACK:
            label = logic.shorten(f"{name} {CONNECTION_BACK[p]}…", logic.OPTION_LABEL_MAX)
            out.append((p + BACK, label, "The other way round"))
    return out


def cut(text: str, limit: int) -> str:
    """`text` within `limit` characters, cut after a whole entry (never inside **bold**)."""
    if len(text) <= limit:
        return text
    head = text[: limit - 1]
    at = max(head.rfind("\n"), head.rfind(", "), head.rfind("; "))
    return (head[:at] if at > 0 else head).rstrip(",; ") + "…"


def _who(names: CampaignLookup, entity_id: str) -> str:
    """A name with its kind, so two buttons never read the same: "Bell (NPC)"."""
    entity = names.entities.get(entity_id)
    return f"{entity.name} ({_kind(entity.type)})" if entity is not None else "?"


def _kind(type_key: str) -> str:
    return KIND_SHORT.get(type_key, type_key)


def _more(items: list[str], sep: str = ", ", *, full: bool = False) -> str:
    if full:
        return sep.join(items)
    shown = sep.join(items[:SHOWN])
    return shown + (f" … and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def card_text(
    lookup: CampaignLookup,
    entity_id: str,
    connections: list[Relation],
    *,
    secrets: bool,
    player: str | None = None,
    sheet: str | None = None,
    full: bool = False,
    limit: int = CARD_MAX,
) -> str | None:
    """The card for one name, or None if it's gone. `player`: who plays it (a player's
    character); `sheet`: its D&D Beyond sheet line (#723). `full`: every entry of each
    section (Show all), not the first few."""
    entity = lookup.entities.get(entity_id)
    if entity is None or entity.status != CONFIRMED:
        return None
    own = name_key(entity.name)
    mine = [e for e in lookup.names if e.entity_id == entity_id and e.confirmed]
    others = [_md(e.text) for e in mine if not e.secret and e.key != own]
    hidden = [_md(e.text) for e in mine if e.secret]
    heard = lookup.heard.get(entity_id)
    when = (
        f"Last heard <t:{heard.last_session_at}:D>"
        if heard is not None and heard.last_session_at
        else "Not heard in a session yet"
    )
    what = f"played by **{_md(player)}**" if player else _kind(entity.type)
    lines = [f"🪪 **{_md(entity.name)}** · {what} · {when}"]
    if sheet:
        lines.append(sheet)
    if others:
        lines.append(f"**Also called:** {_more(others, full=full)}")
    if secrets and hidden:
        lines.append(f"**🤫 Secret:** {_more(hidden, full=full)} (hidden from players)")
    # Grouped by how they're connected, read from this name's side: "is a member of
    # **Frostwolf tribe**; knows **Bell**, **Kesh**".
    groups: dict[str, list[str]] = {}
    for r in connections:
        if r.status != CONFIRMED or (r.secret and not secrets):
            continue
        ahead = r.subject_id == entity_id or r.predicate in BOTH_WAYS
        other = lookup.entities.get(r.object_id if r.subject_id == entity_id else r.subject_id)
        if other is None or other.status != CONFIRMED or other.id == entity_id:
            continue
        words = (
            connection_words(r.predicate)
            if ahead
            else CARD_BACK.get(r.predicate, "is connected to")
        )
        groups.setdefault(words, []).append(f"**{_md(other.name)}**" + (" 🤫" if r.secret else ""))
    if groups:
        said = "; ".join(f"{words} {_more(who, full=full)}" for words, who in groups.items())
        lines.append(f"**Connections:** {said}")
    text = "\n".join(lines)
    return cut(text, limit)


async def _names(interaction: discord.Interaction, campaign: Campaign) -> CampaignLookup | None:
    """The campaign's names from the in-memory copy, or None after saying why."""
    cache = _bot(interaction).lookup
    if cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return None
    try:
        return await cache.get(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't load the campaign's names")
        await _tell(interaction, "DMbot couldn't load the names just now. Try again in a moment.")
        return None


async def show_card(
    interaction: discord.Interaction,
    campaign_id: str,
    entity_id: str,
    *,
    replace: bool = False,
    note: str | None = None,
    full: bool = False,
) -> None:
    """The card, as a new private message or in place of the one pressed; `note` goes
    on top (what just changed). `full`: Show all."""
    await _answer_first(interaction, in_place=replace)  # names may load slowly (#537)
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None:
        return
    if memory is None:  # never leave Discord's "thinking…" up
        await _tell(interaction, NOT_AVAILABLE)
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    connections = await memory.relations(
        campaign.guild_id, campaign.id, entity_id=entity_id, include_secret=secrets
    )
    entity = names.entities.get(entity_id)
    player = sheet = None
    has_sheet = False
    if entity is not None and entity.played_by is not None:
        player = _bot(interaction).name_of(campaign.guild_id, entity.played_by)
        store = _bot(interaction).sheets
        if store is not None:
            from dmbot.ui.sheets import card_line

            found = await store.sheet(campaign.guild_id, campaign.id, entity_id)
            has_sheet = found is not None
            sheet = card_line(found, link=secrets)  # the address: the campaign's DMs only
    note = cut(note, NOTE_MAX) if note else None
    limit = CARD_MAX - (len(note) + 2 if note else 0)
    text = card_text(
        names,
        entity_id,
        connections,
        secrets=secrets,
        player=player,
        sheet=sheet,
        full=full,
        limit=limit,
    )
    if text is None or entity is None:
        if replace:  # never leave the message pressed on a step that's over
            await _replace(interaction, GONE, None)
        else:
            await _tell(interaction, GONE)
        return
    longer = not full and text != card_text(
        names,
        entity_id,
        connections,
        secrets=secrets,
        player=player,
        sheet=sheet,
        full=True,
        limit=limit,
    )
    own = name_key(entity.name)
    others = any(
        e.entity_id == entity_id and e.confirmed and e.key != own and (secrets or not e.secret)
        for e in names.names
    )
    if note:
        text = f"{note}\n\n{text}"
    view = NameCard(campaign.id, entity_id, others=others, longer=longer, sheet=has_sheet)
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


async def _current(
    interaction: discord.Interaction, campaign_id: str, entity_id: str
) -> tuple[Campaign, str] | None:
    """The campaign and the entry's name, if both are still there (tells them if not)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    entity = await memory.entity(campaign.guild_id, campaign.id, entity_id)
    if entity is None or entity.status != CONFIRMED:
        await _tell(interaction, GONE)
        return None
    return campaign, entity.name


class NameCard(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        *,
        others: bool = False,
        longer: bool = False,
        sheet: bool = False,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        grey = discord.ButtonStyle.secondary
        # First row: the three edits the names panel names, in its words (#353). Remove
        # says what it does and still asks first.
        self.add_item(_Button(self._fix, label="✏️ Fix spelling", style=discord.ButtonStyle.primary))
        self.add_item(_Button(self._change_kind, label="Change what it is", style=grey))
        self.add_item(_Button(self._remove, label="🗑 Remove this name", style=grey))
        # "Also called", as the card says: right after Add a name, "Add another name"
        # read like "add the next name" (#353 review).
        self.add_item(_Button(self._add, label=ALSO_CALLED, style=grey, row=1))
        if others:
            self.add_item(_Button(self._edit_others, label="Edit other names", style=grey, row=1))
        self.add_item(_Button(self._same, label="🔗 Same as…", style=grey, row=1))
        # Row 2, so row 1's buttons don't clip on a phone (#393).
        self.add_item(_Button(self._connect, label="🧭 Connect to…", style=grey, row=2))
        if longer:
            self.add_item(_Button(self._all, label="Show all", style=grey, row=2))
        if sheet:
            self.add_item(_Button(self._unlink_sheet, label="📜 Unlink sheet", style=grey, row=2))

    async def _unlink_sheet(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheets import dm_unlink

        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        self.stop()
        gone = await dm_unlink(interaction, campaign.guild_id, campaign.id, self.entity_id)
        note = f"DMbot forgot **{_md(name)}**'s sheet." if gone else None
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True, note=note)

    async def _all(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True, full=True)

    async def _edit_others(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        listed = await _other_names(interaction, campaign, self.entity_id, name)
        if not listed:
            await _tell(
                interaction,
                f"**{_md(name)}** has no other names yet. Press {ALSO_CALLED} to add one.",
            )
            return
        self.stop()
        more = (
            f" Showing the first {logic.SELECT_OPTIONS_MAX}."
            if len(listed) > logic.SELECT_OPTIONS_MAX
            else ""
        )
        await _replace(
            interaction,
            f"**Other names for {_md(name)}:** pick one to change.{more}",
            OtherNames(self.campaign_id, self.entity_id, listed),
        )

    async def _same(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(self.campaign_id, self.entity_id, SAME, f"{found[1]} is the same as…")
            )

    async def _connect(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        names = await _names(interaction, campaign)
        memory = _memory(interaction)
        if names is None or memory is None:
            return
        secrets = sees_secrets(campaign, interaction.user.id)
        removable = []
        for r in await memory.relations(
            campaign.guild_id,
            campaign.id,
            entity_id=self.entity_id,
            confirmed_only=True,
            include_secret=secrets,
        ):
            said = sentence(names, r)
            if said is not None:
                removable.append((r.id, said + (" 🤫" if r.secret else "")))
        self.stop()
        back = " Or take one back with Remove a connection." if removable else ""
        await _replace(
            interaction,
            f"**How is {_md(name)} connected?** Pick how, then the other name.{back}",
            Connect(self.campaign_id, self.entity_id, name, removable),
        )

    async def _fix(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                FixSpellingForm(self.campaign_id, self.entity_id, found[1])
            )

    async def _add(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            secrets = sees_secrets(found[0], interaction.user.id)
            await interaction.response.send_modal(
                AnotherNameForm(self.campaign_id, self.entity_id, found[1], secrets=secrets)
            )

    async def _change_kind(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        self.stop()
        view = KindChange(self.campaign_id, self.entity_id, found[1])
        await _replace(interaction, f"**What is {_md(found[1])}?**", view)

    async def _remove(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found is None:
            return
        campaign, name = found
        memory = _memory(interaction)
        assert memory is not None
        own = name_key(name)
        others = [
            _md(a.text)
            for a in await memory.aliases(
                campaign.guild_id,
                campaign.id,
                entity_id=self.entity_id,
                include_secret=sees_secrets(campaign, interaction.user.id),
            )
            if a.key != own
        ]
        also = f" and its other names ({_more(others)})" if others else ""
        self.stop()
        await _replace(
            interaction,
            f"Forget **{_md(name)}**? DMbot stops listening for it{also}. Past transcripts "
            "don't change.",
            ConfirmRemove(self.campaign_id, self.entity_id),
        )


class FixSpellingForm(discord.ui.Modal, title="Fix spelling"):
    name: discord.ui.TextInput[FixSpellingForm] = discord.ui.TextInput(
        label="Name", max_length=NAME_LIMIT
    )

    def __init__(self, campaign_id: str, entity_id: str, current: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name.default = current[:NAME_LIMIT]

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)  # saves, then reloads names
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, old = found
        new = " ".join(self.name.value.split())
        if name_key(new) != name_key(old):
            clash = await known_as(memory, campaign, new)
            if clash is not None and name_key(clash) != name_key(old):
                await _tell(interaction, already_known(new, clash))
                return
        try:
            written = await memory.rename_entity(
                campaign.guild_id, campaign.id, self.entity_id, new, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        note = f"✏️ Now spelled **{_md(written.value.name)}**."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


class AnotherNameForm(discord.ui.Modal, title="Add another name"):
    other: discord.ui.TextInput[AnotherNameForm] = discord.ui.TextInput(
        label="Other names or nicknames",
        placeholder="Separate with commas. For example: Bell, the old knight",
        required=False,
        max_length=400,
    )
    secret: discord.ui.TextInput[AnotherNameForm] = discord.ui.TextInput(
        label="Disguises or secret identities (optional)",
        placeholder="Example: the hooded stranger. Players never learn who it really is.",
        required=False,
        max_length=400,
    )

    def __init__(self, campaign_id: str, entity_id: str, name: str, *, secrets: bool) -> None:
        super().__init__(title=logic.shorten(f"Other names for {name}", 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        if not secrets:  # only the campaign's DMs deal in secret names
            self.remove_item(self.secret)
            self.other.required = True

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)  # saves, then reloads names
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, name = found
        secret_ok = sees_secrets(campaign, interaction.user.id)
        plain = split_names(self.other.value)
        hidden = split_names(self.secret.value) if secret_ok else []
        if not plain and not hidden:
            await _tell(interaction, "Nothing added. Type at least one name.")
            return
        gid, cid = campaign.guild_id, campaign.id
        added: list[str] = []
        kept: list[str] = []
        problems: list[str] = []
        for text, secret in [*((t, False) for t in plain), *((t, True) for t in hidden)]:
            clash = None if secret else await known_as(memory, campaign, text)
            if clash is not None and name_key(clash) != name_key(name):
                problems.append(f"**{_md(text)}** is already {_md(clash)}'s")
                continue
            try:
                await memory.add_alias(
                    gid,
                    cid,
                    self.entity_id,
                    text,
                    kind="title" if secret else "nickname",
                    source=DM,
                    status=CONFIRMED,
                    secret=secret,
                )
            except MemoryRuleError as exc:
                problems.append(f"**{_md(text)}**: {exc}")
                continue
            (kept if secret else added).append(f"**{_md(text)}**")
        changed(interaction, campaign)
        lines = []
        if added:
            lines.append(f"✅ DMbot now also listens for {', '.join(added)} as **{_md(name)}**.")
        if kept:
            lines.append(
                f"🤫 Kept secret: {', '.join(kept)}. Hidden from players. DMbot never puts it "
                "in the transcript."
            )
        if problems:
            lines.append(f"Couldn't add {'; '.join(problems)}.")
        await show_card(interaction, cid, self.entity_id, replace=True, note="\n".join(lines))


class KindChange(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, name: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.pick = _Select(
            self._picked,
            placeholder="Pick one…",
            options=[discord.SelectOption(label=label, value=k) for k, label in KINDS.items()],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        kind = self.pick.values[0]
        if kind == PC:
            self.clear_items()
            self.add_item(_UserSelect(self._player_picked))
            self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
            await _replace(interaction, f"**Who plays {_md(self.name)}?**", self)
            return
        try:
            await memory.set_entity_type(
                campaign.guild_id, campaign.id, self.entity_id, kind, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)

    async def _player_picked(
        self, interaction: discord.Interaction, player: discord.User | discord.Member
    ) -> None:
        if player.bot:
            await _tell(interaction, "Pick a person, not a bot.")
            return
        await _answer_first(interaction, in_place=True)
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        try:
            await memory.confirm_entity(
                campaign.guild_id, campaign.id, self.entity_id, PC, source=DM, played_by=player.id
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class ConfirmRemove(_Menu):
    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.add_item(_Button(self._forget, label="Forget it", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label="Keep it", style=discord.ButtonStyle.secondary))

    async def _forget(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)  # still there?
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, _ = found
        try:
            written = await memory.set_entity_status(
                campaign.guild_id, campaign.id, self.entity_id, REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        self.stop()
        view = discord.ui.View(timeout=None)
        if written.batch is not None:
            view.add_item(UndoButton(campaign.id, self.entity_id, written.batch))
        await interaction.response.edit_message(
            content=f"Forgot **{_md(written.value.name)}**. Wrong? Press **Undo** within "
            f"{days(memory.keep_days)} to bring it back.",
            view=view,
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:undo:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo forgetting a name, or a 🔗 Same as… (`entity` is the name that went); keeps
    working after a restart (all it needs is in its ID, under Discord's 100 characters).
    Only acts while that name is still forgotten or joined to another."""

    def __init__(self, campaign_id: str, entity_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:undo:{campaign_id}:{entity_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(match["campaign"], match["entity"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        entity = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
        if entity is None or entity.status not in (REJECTED, MERGED):
            await _tell(interaction, "Nothing to undo: that was already undone or changed.")
            return
        # Answer Discord at once (#351): a big undo can take longer than its 3 seconds.
        # Saying so in place also takes the button away, so it isn't pressed twice.
        await _replace(interaction, f"↩️ Undoing… bringing back **{_md(entity.name)}**.", None)
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError as exc:
            now = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
            if now is not None and now.status not in (REJECTED, MERGED):  # a second press
                await _replace(interaction, f"↩️ **{_md(entity.name)}** is already back.", None)
                return
            reason = (
                str(exc)  # too old (#164)
                if isinstance(exc, TooLateToUndo)
                else f"Couldn't undo: **{_md(entity.name)}** was changed again since."
            )
            await _replace(
                interaction,
                f"{reason} "
                + (
                    "Add it again with ➕ Add a name on the names panel (its other names and "
                    "connections need adding again too)."
                    if entity.status == REJECTED
                    else "To split them, open the card's Edit other names, press ✖ Not this "
                    "name, add it again with ➕ Add a name, then fix any connections."
                ),
                None,
            )
            return
        except Exception:
            log.exception("Undo of batch %s failed", self.batch)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        note = f"↩️ **{_md(entity.name)}** is back, with its other names and connections."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


# ---- other names: ⭐ main name, 🤫 secret, ✖ not this name -------------------------------


async def _other_names(
    interaction: discord.Interaction, campaign: Campaign, entity_id: str, name: str
) -> list[Alias]:
    """The entry's confirmed other names this person may see (secret ones only for the
    campaign's DMs), not its main name."""
    memory = _memory(interaction)
    assert memory is not None
    own = name_key(name)
    return [
        a
        for a in await memory.aliases(
            campaign.guild_id,
            campaign.id,
            entity_id=entity_id,
            include_secret=sees_secrets(campaign, interaction.user.id),
        )
        if a.status == CONFIRMED and a.key != own
    ]


class OtherNames(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, listed: list[Alias]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.pick = _Select(
            self._picked,
            placeholder="Pick a name to change…",
            options=[
                discord.SelectOption(
                    label=logic.shorten(a.text, logic.OPTION_LABEL_MAX),
                    value=a.id,
                    description="🤫 Secret: hidden from players" if a.secret else "Another name",
                )
                for a in listed[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.pick.values[0])
        if found is None:
            return
        campaign, alias, name = found
        self.stop()
        secrets = sees_secrets(campaign, interaction.user.id)
        hidden = (
            " 🤫 Secret: hidden from players, so it can't be the main name." if alias.secret else ""
        )
        await _replace(
            interaction,
            f"**{_md(alias.text)}** is another name for **{_md(name)}**.{hidden}",
            OneName(self.campaign_id, self.entity_id, alias, secrets=secrets),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


async def _one_name(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, alias_id: str
) -> tuple[Campaign, Alias, str] | None:
    """The campaign, the other name and the entry's name, checked again (tells them if
    it's gone). A secret name is never found for someone who may not see it."""
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return None
    campaign, name = found
    for alias in await _other_names(interaction, campaign, entity_id, name):
        if alias.id == alias_id:
            return campaign, alias, name
    await _tell(interaction, "That name isn't there any more.")
    return None


class OneName(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, alias: Alias, *, secrets: bool) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.alias_id = alias.id
        grey = discord.ButtonStyle.secondary
        self.add_item(
            _Button(
                self._main,
                label="⭐ Make it the main name",
                style=discord.ButtonStyle.primary,
                disabled=alias.secret,  # the main name is the one everyone sees
            )
        )
        if secrets:  # only the campaign's DMs deal in secret names
            label = "👁️ Stop keeping it secret" if alias.secret else "🤫 Keep it secret"
            self.add_item(_Button(self._secret, label=label, style=grey))
        self.add_item(_Button(self._not_this, label="✖ Not this name", style=grey))
        self.add_item(_Button(self._back, label="Back", style=grey))

    async def _main(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, old = found
        try:
            await memory.set_main_name(
                campaign.guild_id, campaign.id, self.entity_id, alias.id, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"⭐ **{_md(alias.text)}** is the main name now. **{_md(old)}** is still one of "
            "its other names."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _secret(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, _ = found
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the campaign's DMs can change secret names.")
            return
        try:
            await memory.update_alias(
                campaign.guild_id, campaign.id, alias.id, secret=not alias.secret, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That name isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"👁️ **{_md(alias.text)}** isn't secret any more. Players can see it."
            if alias.secret
            else f"🤫 **{_md(alias.text)}** is secret now: hidden from players from now on, "
            "and DMbot leaves it out of new transcripts. Past transcripts don't change."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _not_this(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _one_name(interaction, self.campaign_id, self.entity_id, self.alias_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign, alias, name = found
        try:
            await memory.update_alias(
                campaign.guild_id, campaign.id, alias.id, status=REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That name isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        note = (
            f"✖ DMbot stops listening for **{_md(alias.text)}** as **{_md(name)}**. Wrong? "
            f"Press {ALSO_CALLED} to add it back."
        )
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


# ---- 🔗 Same as… and 🧭 Connect to…: pick the other name --------------------------------

SAME = "same"


class PickForm(discord.ui.Modal, title="Find the other name"):
    """Type the other name, for Same as… (`how` is SAME) or Connect to… (`how` is the
    connection, maybe ending in BACK)."""

    typed: discord.ui.TextInput[PickForm] = discord.ui.TextInput(
        label="The other name",
        placeholder="Type part of a name, a nickname, or how it sounds",
        max_length=NAME_LIMIT,
    )

    def __init__(self, campaign_id: str, entity_id: str, how: str, title: str) -> None:
        super().__init__(title=logic.shorten(title, 45), timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.how = how

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_picks(interaction, self.campaign_id, self.entity_id, self.how, self.typed.value)


def _asked(name: str, how: str) -> str:
    """The question being answered: "Belleros is the same as…", "Ulfgar is a member of…"."""
    if how == SAME:
        return f"{name} is the same as…"
    predicate = how.removesuffix(BACK)
    words = CONNECTION_BACK.get(predicate) if how.endswith(BACK) else None
    return f"{name} {words or connection_words(predicate)}…"


async def show_picks(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, how: str, typed: str
) -> None:
    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    if found is None:
        return
    campaign, name = found
    names = await _names(interaction, campaign)
    if names is None:
        return
    matches = [
        m
        for m in find(names, typed, secrets=sees_secrets(campaign, interaction.user.id))
        if m.entity_id != entity_id
    ]
    options = [discord.SelectOption(label=_label(names, m), value=m.entity_id) for m in matches]
    asked = f"**{_md(_asked(name, how))}**"
    if not matches:
        text = (
            f"{asked}\nNo other name like **{_md(typed)}** yet. Search again, or add it "
            "with ➕ Add a name on the names panel and come back."
        )
    else:
        text = f"{asked} Pick one."
    await _replace(interaction, text, PickOther(campaign_id, entity_id, how, name, options))


class PickOther(_Menu):
    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        how: str,
        name: str,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.how = how
        self.name = name
        if options:
            self.pick = _Select(self._picked, placeholder="Pick the other name…", options=options)
            self.add_item(self.pick)
        self.add_item(
            _Button(self._again, label="🔍 Search again", style=discord.ButtonStyle.secondary)
        )
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))

    async def _picked(self, interaction: discord.Interaction) -> None:
        other_id = self.pick.values[0]
        await _answer_first(interaction, in_place=True)
        if self.how == SAME:
            found = await _current(interaction, self.campaign_id, self.entity_id)
            other = await _current(interaction, self.campaign_id, other_id) if found else None
            if found is None or other is None:
                return
            found_names = await _names(interaction, found[0])
            if found_names is None:
                return
            self.stop()
            name, other_name = found[1], other[1]
            await _replace(
                interaction,
                f"Make **{_md(name)}** and **{_md(other_name)}** one? Their other names and "
                "connections go together. Which name should it keep?\n"
                "Is one of them a disguise? Press **No**, then press "
                f"{ALSO_CALLED} on the real one to add it as a secret name.",
                SameConfirm(
                    self.campaign_id,
                    self.entity_id,
                    other_id,
                    _who(found_names, self.entity_id),
                    _who(found_names, other_id),
                ),
            )
            return
        self.stop()
        await connect(interaction, self.campaign_id, self.entity_id, self.how, other_id)

    async def _again(self, interaction: discord.Interaction) -> None:
        if await _current(interaction, self.campaign_id, self.entity_id):
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(self.campaign_id, self.entity_id, self.how, _asked(self.name, self.how))
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class SameConfirm(_Menu):
    def __init__(
        self, campaign_id: str, entity_id: str, other_id: str, name: str, other_name: str
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.other_id = other_id
        blue = discord.ButtonStyle.primary
        for handler, label in ((self._keep_this, name), (self._keep_other, other_name)):
            self.add_item(
                _Button(handler, label=logic.shorten(f"Keep the name {label}", 80), style=blue)
            )
        self.add_item(
            _Button(self._back, label="No, they're different", style=discord.ButtonStyle.secondary)
        )

    async def _keep_this(self, interaction: discord.Interaction) -> None:
        await self._join(interaction, self.entity_id, self.other_id)

    async def _keep_other(self, interaction: discord.Interaction) -> None:
        await self._join(interaction, self.other_id, self.entity_id)

    async def _join(self, interaction: discord.Interaction, keep_id: str, gone_id: str) -> None:
        kept = await _current(interaction, self.campaign_id, keep_id)
        gone = await _current(interaction, self.campaign_id, gone_id) if kept else None
        memory = _memory(interaction)
        if kept is None or gone is None or memory is None:
            return
        campaign = kept[0]
        # Answer Discord at once (#351): a big merge can take longer than its 3 seconds.
        # Saying so in place also takes the buttons away, so nothing is pressed twice.
        self.stop()
        await _replace(interaction, f"🔗 Joining **{_md(gone[1])}** into **{_md(kept[1])}**…", None)
        try:
            written = await memory.merge(
                campaign.guild_id, campaign.id, keep_id, gone_id, source=DM, dm_said_same=True
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't join them. {exc} Nothing was changed.", None)
            return
        except Exception:
            log.exception("Joining %s into %s failed", gone_id, keep_id)
            await _replace(interaction, NOTHING_CHANGED, None)
            return
        changed(interaction, campaign)
        note = f"🔗 Done: **{_md(gone[1])}** is now another name for **{_md(kept[1])}**."
        await show_card(interaction, campaign.id, keep_id, replace=True, note=note)
        if written.batch is not None:
            # Its own message, so Undo outlives the card's menu (and a restart).
            view = discord.ui.View(timeout=None)
            view.add_item(UndoButton(campaign.id, gone_id, written.batch))
            await interaction.followup.send(
                f"Wrong? Press **Undo** within {days(memory.keep_days)} to split "
                f"**{_md(gone[1])}** and **{_md(kept[1])}** again.",
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class Connect(_Menu):
    def __init__(
        self, campaign_id: str, entity_id: str, name: str, removable: list[tuple[str, str]]
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.name = name
        self.how = _Select(
            self._how_picked,
            placeholder=logic.shorten(f"How is {name} connected?", 150),
            options=[
                discord.SelectOption(label=label, value=v, description=d)
                for v, label, d in connect_choices(name)
            ],
        )
        self.add_item(self.how)
        if removable:
            self.remove = _Select(
                self._remove_picked,
                placeholder="Remove a connection…",
                options=[
                    discord.SelectOption(label=logic.shorten(said, logic.OPTION_LABEL_MAX), value=r)
                    for r, said in removable[: logic.SELECT_OPTIONS_MAX]
                ],
                row=1,
            )
            self.add_item(self.remove)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary, row=2))

    async def _how_picked(self, interaction: discord.Interaction) -> None:
        how = self.how.values[0]
        if await _current(interaction, self.campaign_id, self.entity_id):
            self.origin = None  # the form's answer replaces this message; cancel keeps it
            await interaction.response.send_modal(
                PickForm(self.campaign_id, self.entity_id, how, _asked(self.name, how))
            )

    async def _remove_picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _current(interaction, self.campaign_id, self.entity_id)
        memory = _memory(interaction)
        if found is None or memory is None:
            return
        campaign = found[0]
        names = await _names(interaction, campaign)
        if names is None:
            return
        wanted = self.remove.values[0]
        mine = await memory.relations(
            campaign.guild_id,
            campaign.id,
            entity_id=self.entity_id,
            confirmed_only=True,
            include_secret=sees_secrets(campaign, interaction.user.id),
        )
        relation = next((r for r in mine if r.id == wanted), None)
        if relation is None:
            await _tell(interaction, "That connection isn't there any more.")
            return
        try:
            await memory.update_relation(
                campaign.guild_id, campaign.id, relation.id, status=REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, "That connection isn't there any more.")
            return
        changed(interaction, campaign)
        self.stop()
        said = sentence(names, relation) or "that connection"
        note = f"Removed: {_md(said)}. Wrong? Press 🧭 Connect to… to add it again."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


async def connect(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, how: str, other_id: str
) -> None:
    """Save the connection the DM picked (confirmed: the DM said it), then the card."""
    await _answer_first(interaction, in_place=True)
    found = await _current(interaction, campaign_id, entity_id)
    other = await _current(interaction, campaign_id, other_id) if found else None
    memory = _memory(interaction)
    if found is None or other is None or memory is None:
        return
    campaign = found[0]
    predicate = how.removesuffix(BACK)
    if predicate not in CONNECTION_WORDS:
        await _tell(interaction, "Pick how they're connected again.")
        return
    subject, obj = (other, found) if how.endswith(BACK) else (found, other)
    subject_id, object_id = (other_id, entity_id) if how.endswith(BACK) else (entity_id, other_id)
    try:
        written = await memory.add_relation(
            campaign.guild_id,
            campaign.id,
            subject_id,
            predicate,
            object_id,
            source=DM,
            confidence=1.0,
            status=CONFIRMED,
        )
    except MemoryRuleError as exc:
        await _tell(interaction, f"Couldn't connect them. {exc}")
        return
    changed(interaction, campaign)
    _, flags = written.value
    note = f"🧭 Saved: **{_md(subject[1])}** {connection_words(predicate)} **{_md(obj[1])}**."
    if flags:
        note += (
            f' It\'s an odd pair for "{connection_words(predicate)}", so DMbot will ask you '
            "about it after the session."
        )
    await show_card(interaction, campaign.id, entity_id, replace=True, note=note)


# ---- finding a name --------------------------------------------------------------------


class FindForm(discord.ui.Modal, title="Find a name"):
    typed: discord.ui.TextInput[FindForm] = discord.ui.TextInput(
        label="Name",
        placeholder="Type part of a name, a nickname, or how it sounds",
        max_length=NAME_LIMIT,
    )

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_matches(interaction, self.campaign_id, self.typed.value)


def _label(names: CampaignLookup, m: Match) -> str:
    entity = names.entities[m.entity_id]
    matched = "" if name_key(m.matched) == name_key(entity.name) else f' (matched "{m.matched}")'
    secret = " 🤫" if m.secret else ""
    return logic.shorten(f"{entity.name} · {_kind(entity.type)}{matched}{secret}", 100)


async def show_matches(interaction: discord.Interaction, campaign_id: str, typed: str) -> None:
    await _answer_first(interaction)  # names may load slowly (#537)
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    matches = find(names, typed, secrets=sees_secrets(campaign, interaction.user.id))
    if len(matches) == 1:
        await show_card(interaction, campaign.id, matches[0].entity_id)
        return
    options = [discord.SelectOption(label=_label(names, m), value=m.entity_id) for m in matches]
    view = Matches(campaign.id, typed, options)
    if not matches:
        text = f"No name like **{_md(typed)}**."
    elif len(matches) >= MAX_RESULTS:
        text = (
            f"**Names like {_md(typed)}:** the closest {MAX_RESULTS}. Type more of the name to "
            "narrow it."
        )
    else:
        text = f"**Names like {_md(typed)}:** pick one."
    await _send(interaction, text, view)


class Matches(_Menu):
    def __init__(self, campaign_id: str, typed: str, options: list[discord.SelectOption]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.typed = typed
        if options:
            self.pick = _Select(
                self._picked, placeholder="✏️ Fix, change or remove a name…", options=options
            )
            self.add_item(self.pick)
        primary, secondary = discord.ButtonStyle.primary, discord.ButtonStyle.secondary
        self.add_item(
            _Button(self._again, label="🔍 Search again", style=primary if options else secondary)
        )
        self.add_item(
            _Button(
                self._add,
                label="➕ Add it as a new name",
                style=secondary if options else primary,
            )
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.pick.values[0], replace=True)

    async def _again(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(FindForm(self.campaign_id))

    async def _add(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.names import AddNameForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            form = AddNameForm(
                self.campaign_id, secrets=sees_secrets(campaign, interaction.user.id)
            )
            form.name.default = self.typed[:NAME_LIMIT]
            await interaction.response.send_modal(form)


# ---- the type-ahead for /dmbot names find: ---------------------------------------------


async def typeahead_campaign(interaction: discord.Interaction) -> Campaign | None:
    """The campaign the type-ahead searches, or None (it suggests nothing): the one
    being played, else the person's only campaign. Same check as the panel."""
    guild = interaction.guild
    if guild is None:
        return None
    bot = _bot(interaction)
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    playing = bot.active_campaign_id(guild.id)
    for campaign in mine:
        if campaign.id == playing:
            return campaign
    return mine[0] if len(mine) == 1 else None


async def find_typeahead(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Discord asks this as someone types; it runs for anyone who can see the command,
    so it suggests nothing to anyone who isn't the campaign's DM or a server manager."""
    try:
        campaign = await typeahead_campaign(interaction)
        cache = _bot(interaction).lookup
        if campaign is None or cache is None:
            return []
        names = await cache.get(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Name type-ahead failed")
        return []
    secrets = sees_secrets(campaign, interaction.user.id)
    if current.strip():
        matches = find(names, current, secrets=secrets)
    else:
        matches = [
            Match(e, names.entities[e].name, False, 0) for e in said_lately(names, MAX_RESULTS)
        ]
    return [
        app_commands.Choice(name=_label(names, m), value=m.entity_id) for m in matches[:MAX_RESULTS]
    ]


async def open_found(interaction: discord.Interaction, campaign_id: str, value: str) -> None:
    """What `/dmbot names find:` does with what was picked (an ID) or typed (search)."""
    if is_id(value):
        await show_card(interaction, campaign_id, value)
    else:
        await show_matches(interaction, campaign_id, value)
