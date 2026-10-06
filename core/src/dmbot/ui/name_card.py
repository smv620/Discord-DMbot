"""Find a name and its card (docs/PLAN.md, "Names at scale", step 2; #126).

🔍 Find a name (a one-field form, or `/dmbot names find:` with Discord's type-ahead)
opens a **name card**: what it is, when it was last heard, its other names, its secret
names (the campaign's DMs only) and its connections, with ✏️ Fix spelling, Add another
name, Change what it is, and Remove (asks first; Undo). Everything answers from the
in-memory copy of the names. Only the campaign's DMs and server managers, privately;
every press checks again.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, REJECTED, MemoryRuleError, Relation, is_id
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
    split_names,
)

log = logging.getLogger(__name__)

CARD_MAX = 1900  # under Discord's 2,000 characters
SHOWN = 3  # per section, then "… and N more"
NAME_LIMIT = 100

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


def sees_secrets(campaign: Campaign, user_id: int) -> bool:
    """Secret names are for the campaign's DMs only: a server manager who isn't one of
    them may be at the table."""
    return user_id in campaign.dm_user_ids


def _kind(type_key: str) -> str:
    return KIND_SHORT.get(type_key, type_key)


def _more(items: list[str]) -> str:
    shown = ", ".join(items[:SHOWN])
    return shown + (f" … and {len(items) - SHOWN} more" if len(items) > SHOWN else "")


def card_text(
    lookup: CampaignLookup, entity_id: str, connections: list[Relation], *, secrets: bool
) -> str | None:
    """The card for one name, or None if it's gone."""
    entity = lookup.entities.get(entity_id)
    if entity is None or entity.status != CONFIRMED:
        return None
    others = [
        _md(e.text)
        for e in lookup.names
        if e.entity_id == entity_id and e.confirmed and not e.secret and e.text != entity.name
    ]
    hidden = [_md(e.text) for e in lookup.names if e.entity_id == entity_id and e.secret]
    heard = lookup.heard.get(entity_id)
    when = (
        f"Last heard <t:{heard.last_session_at}:D>"
        if heard is not None and heard.last_session_at
        else "Not heard in a session yet"
    )
    lines = [f"🪪 **{_md(entity.name)}** · {_kind(entity.type)} · {when}"]
    if others:
        lines.append(f"**Also called:** {_more(others)}")
    if secrets and hidden:
        lines.append(f"**🤫 Secret:** {_more(hidden)} (only you see this)")
    sentences = []
    for r in connections:
        other = r.object_id if r.subject_id == entity_id else r.subject_id
        if r.status != CONFIRMED or (r.secret and not secrets) or other not in lookup.entities:
            continue
        words = CONNECTION_WORDS.get(r.predicate, r.predicate.replace("_", " "))
        subject = lookup.entities[r.subject_id].name
        obj = lookup.entities[r.object_id].name
        sentences.append(f"{_md(subject)} {words} {_md(obj)}" + (" 🤫" if r.secret else ""))
    if sentences:
        lines.append(f"**Connections:** {_more(sentences)}")
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
    interaction: discord.Interaction, campaign_id: str, entity_id: str, *, replace: bool = False
) -> None:
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
    text = card_text(names, entity_id, connections, secrets=secrets)
    if text is None:
        await _tell(interaction, GONE)
        return
    view = NameCard(campaign.id, entity_id, secrets=secrets)
    if replace:
        await _replace(interaction, text, view)
    else:
        await _send(interaction, text, view)


class NameCard(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, *, secrets: bool) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.secrets = secrets
        self.add_item(_Button(self._fix, label="✏️ Fix spelling", style=discord.ButtonStyle.primary))
        self.add_item(
            _Button(self._add, label="Add another name", style=discord.ButtonStyle.secondary)
        )
        self.add_item(
            _Button(self._kind, label="Change what it is", style=discord.ButtonStyle.secondary)
        )
        self.add_item(_Button(self._remove, label="Remove", style=discord.ButtonStyle.secondary))

    async def _entity_name(self, interaction: discord.Interaction) -> tuple[Campaign, str] | None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return None
        entity = await memory.entity(campaign.guild_id, campaign.id, self.entity_id)
        if entity is None or entity.status != CONFIRMED:
            await _tell(interaction, GONE)
            return None
        return campaign, entity.name

    async def _fix(self, interaction: discord.Interaction) -> None:
        found = await self._entity_name(interaction)
        if found:
            await interaction.response.send_modal(
                FixSpellingForm(self.campaign_id, self.entity_id, found[1])
            )

    async def _add(self, interaction: discord.Interaction) -> None:
        found = await self._entity_name(interaction)
        if found:
            secrets = sees_secrets(found[0], interaction.user.id)
            await interaction.response.send_modal(
                AnotherNameForm(self.campaign_id, self.entity_id, found[1], secrets=secrets)
            )

    async def _kind(self, interaction: discord.Interaction) -> None:
        found = await self._entity_name(interaction)
        if found is None:
            return
        self.stop()
        view = KindChange(self.campaign_id, self.entity_id, found[1])
        await _replace(interaction, f"**What is {_md(found[1])}?**", view)

    async def _remove(self, interaction: discord.Interaction) -> None:
        found = await self._entity_name(interaction)
        if found is None:
            return
        campaign, name = found
        memory = _memory(interaction)
        assert memory is not None
        others = [
            a.text
            for a in await memory.aliases(
                campaign.guild_id,
                campaign.id,
                entity_id=self.entity_id,
                include_secret=sees_secrets(campaign, interaction.user.id),
            )
            if a.text != name
        ]
        also = f" and its other names ({_more([_md(o) for o in others])})" if others else ""
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
        self.name.default = current

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        try:
            written = await memory.rename_entity(
                campaign.guild_id, campaign.id, self.entity_id, self.name.value, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it: {exc}")
            return
        await _tell(interaction, f"✏️ Now spelled **{_md(written.value.name)}**.")


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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        secret_ok = sees_secrets(campaign, interaction.user.id)
        plain = split_names(self.other.value)
        hidden = split_names(self.secret.value) if secret_ok else []
        if not plain and not hidden:
            await _tell(interaction, "Nothing to add: type at least one name.")
            return
        gid, cid = campaign.guild_id, campaign.id
        try:
            for text in plain:
                await memory.add_alias(
                    gid, cid, self.entity_id, text, kind="nickname", source=DM, status=CONFIRMED
                )
            for text in hidden:
                await memory.add_alias(
                    gid,
                    cid,
                    self.entity_id,
                    text,
                    kind="title",
                    source=DM,
                    status=CONFIRMED,
                    secret=True,
                )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't add that: {exc}")
            return
        added = ", ".join(f"**{_md(t)}**" for t in plain)
        lines = [f"✅ DMbot also listens for {added}." if plain else ""]
        if hidden:
            lines.append(
                f"🤫 Kept secret: {', '.join(f'**{_md(t)}**' for t in hidden)}. Only you see "
                "this. DMbot never puts it in the transcript."
            )
        await _tell(interaction, "\n".join(line for line in lines if line))


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
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it: {exc}")
            return
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
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it: {exc}")
            return
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
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        try:
            written = await memory.set_entity_status(
                campaign.guild_id, campaign.id, self.entity_id, REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        self.stop()
        view = discord.ui.View(timeout=None)
        if written.batch is not None:
            view.add_item(UndoButton(campaign.id, written.batch))
        await interaction.response.edit_message(
            content=f"Forgot **{_md(written.value.name)}**.", view=view
        )

    async def _keep(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class UndoButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:undo:(?P<campaign>[0-9a-f]{32}):(?P<batch>[0-9]{1,18})",
):
    """Undo a removal; keeps working after a restart (campaign and change in the ID)."""

    def __init__(self, campaign_id: str, batch: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Undo",
                style=discord.ButtonStyle.secondary,
                custom_id=f"dmbot:names:undo:{campaign_id}:{batch}",
            )
        )
        self.campaign_id = campaign_id
        self.batch = batch

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> UndoButton:
        return cls(match["campaign"], int(match["batch"]))

    async def callback(self, interaction: discord.Interaction) -> Any:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        try:
            await memory.undo(campaign.guild_id, campaign.id, self.batch)
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't undo it: {exc}")
            return
        await interaction.response.edit_message(content="↩️ Undone: it's back.", view=None)


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


def _option(names: CampaignLookup, m: Match) -> discord.SelectOption:
    entity = names.entities[m.entity_id]
    matched = "" if m.matched == entity.name else f' (matched "{m.matched}")'
    secret = " 🤫" if m.secret else ""
    return discord.SelectOption(
        label=logic.shorten(f"{entity.name} · {_kind(entity.type)}{matched}{secret}", 100),
        value=m.entity_id,
    )


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
    view = Matches(campaign.id, typed, [_option(names, m) for m in matches])
    if matches:
        text = f"**Names like {_md(typed)}:** pick one."
    else:
        text = f"No name like **{_md(typed)}**."
    await _send(interaction, text, view)


class Matches(_Menu):
    def __init__(self, campaign_id: str, typed: str, options: list[discord.SelectOption]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.typed = typed
        if options:
            self.pick = _Select(self._picked, placeholder="Open a name…", options=options)
            self.add_item(self.pick)
        self.add_item(
            _Button(self._again, label="🔍 Search again", style=discord.ButtonStyle.primary)
        )
        self.add_item(
            _Button(self._add, label="➕ Add it as a new name", style=discord.ButtonStyle.secondary)
        )

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.pick.values[0], replace=True)

    async def _again(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(FindForm(self.campaign_id))

    async def _add(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.names import AddNameForm

        if await _campaign_for(interaction, self.campaign_id):
            form = AddNameForm(self.campaign_id)
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
        app_commands.Choice(name=_option(names, m).label, value=m.entity_id)
        for m in matches[:MAX_RESULTS]
    ]


async def open_found(interaction: discord.Interaction, campaign_id: str, value: str) -> None:
    """What `/dmbot names find:` does with what was picked (an ID) or typed (search)."""
    if is_id(value):
        await show_card(interaction, campaign_id, value)
    else:
        await show_matches(interaction, campaign_id, value)
