"""Find a name and its card (docs/PLAN.md, "Names at scale", step 2; #126).

🔍 Find a name (a one-field form, or `/dmbot names find:` with Discord's type-ahead)
opens a **name card**: what it is, when it was last heard, its other names, its secret
names (the campaign's DMs only) and its connections, with ✏️ Fix spelling, Add another
name, Change what it is, and Remove (asks first; Undo). Everything answers from the
in-memory copy of the names, refreshed right after DMbot's own changes. Only the
campaign's DMs and server managers, privately; every press checks again.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, REJECTED, MemoryRuleError, Relation, is_id, name_key
from dmbot.memory.search import MAX_RESULTS, Match, find, said_lately
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _bot,
    _Button,
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

CARD_MAX = 1900  # under Discord's 2,000 characters
SHOWN = 3  # per section, then "… and N more"
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


def _kind(type_key: str) -> str:
    return KIND_SHORT.get(type_key, type_key)


def _more(items: list[str], sep: str = ", ") -> str:
    shown = sep.join(items[:SHOWN])
    return shown + (f" … and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def card_text(
    lookup: CampaignLookup,
    entity_id: str,
    connections: list[Relation],
    *,
    secrets: bool,
    player: str | None = None,
) -> str | None:
    """The card for one name, or None if it's gone. `player`: who plays it (a player's
    character)."""
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
    if others:
        lines.append(f"**Also called:** {_more(others)}")
    if secrets and hidden:
        lines.append(f"**🤫 Secret:** {_more(hidden)} (hidden from players)")
    sentences = []
    for r in connections:
        subject, obj = lookup.entities.get(r.subject_id), lookup.entities.get(r.object_id)
        if r.status != CONFIRMED or (r.secret and not secrets):
            continue
        if subject is None or obj is None:
            continue
        if subject.status != CONFIRMED or obj.status != CONFIRMED:
            continue
        words = CONNECTION_WORDS.get(r.predicate, r.predicate.replace("_", " "))
        sentences.append(
            f"{_md(subject.name)} {words} {_md(obj.name)}" + (" 🤫" if r.secret else "")
        )
    if sentences:
        lines.append(f"**Connections:** {_more(sentences, '; ')}")
    text = "\n".join(lines)
    return text if len(text) <= CARD_MAX else text[: CARD_MAX - 1] + "…"


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
) -> None:
    """The card, as a new private message or in place of the one pressed; `note` goes
    on top (what just changed)."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return
    names = await _names(interaction, campaign)
    if names is None:
        return
    secrets = sees_secrets(campaign, interaction.user.id)
    connections = await memory.relations(
        campaign.guild_id, campaign.id, entity_id=entity_id, include_secret=secrets
    )
    entity = names.entities.get(entity_id)
    player = None
    if entity is not None and entity.played_by is not None:
        player = _bot(interaction).name_of(campaign.guild_id, entity.played_by)
    text = card_text(names, entity_id, connections, secrets=secrets, player=player)
    if text is None:
        await _tell(interaction, GONE)
        return
    if note:
        text = f"{note}\n\n{text}"
    view = NameCard(campaign.id, entity_id)
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
    def __init__(self, campaign_id: str, entity_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.add_item(_Button(self._fix, label="✏️ Fix spelling", style=discord.ButtonStyle.primary))
        self.add_item(
            _Button(self._add, label="Add another name", style=discord.ButtonStyle.secondary)
        )
        self.add_item(
            _Button(
                self._change_kind, label="Change what it is", style=discord.ButtonStyle.secondary
            )
        )
        self.add_item(_Button(self._remove, label="Remove", style=discord.ButtonStyle.secondary))

    async def _fix(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.stop()
            await interaction.response.send_modal(
                FixSpellingForm(self.campaign_id, self.entity_id, found[1])
            )

    async def _add(self, interaction: discord.Interaction) -> None:
        found = await _current(interaction, self.campaign_id, self.entity_id)
        if found:
            self.stop()
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

    async def on_submit(self, interaction: discord.Interaction) -> None:
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

    async def on_submit(self, interaction: discord.Interaction) -> None:
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
            content=f"Forgot **{_md(written.value.name)}**.", view=view
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:undo:(?P<campaign>[0-9a-f]{32}):(?P<entity>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo forgetting a name; keeps working after a restart (all it needs is in its ID,
    under Discord's 100 characters). Only undoes forgetting that name."""

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
        if entity is None or entity.status != REJECTED:
            await _tell(interaction, "Nothing to undo: that name isn't forgotten any more.")
            return
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError:
            await _tell(
                interaction,
                f"Couldn't undo: **{_md(entity.name)}** was changed again after it was "
                "forgotten. Add it again with ➕ Add a name.",
            )
            return
        changed(interaction, campaign)
        note = f"↩️ **{_md(entity.name)}** is back, with its other names and connections."
        await show_card(interaction, campaign.id, self.entity_id, replace=True, note=note)


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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await show_matches(interaction, self.campaign_id, self.typed.value)


def _label(names: CampaignLookup, m: Match) -> str:
    entity = names.entities[m.entity_id]
    matched = "" if name_key(m.matched) == name_key(entity.name) else f' (matched "{m.matched}")'
    secret = " 🤫" if m.secret else ""
    return logic.shorten(f"{entity.name} · {_kind(entity.type)}{matched}{secret}", 100)


async def show_matches(interaction: discord.Interaction, campaign_id: str, typed: str) -> None:
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
            self.pick = _Select(self._picked, placeholder="Open a name…", options=options)
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
