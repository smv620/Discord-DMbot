"""`/dmbot names`: the names DMbot knows for a campaign (#126, step 3).

The DM adds names, nicknames, secret identities and players' characters, and checks
the names DMbot suggests after a session. Plain words only ("names DMbot knows"),
never "entity", "graph" or "ontology". Only the campaign's DMs (or a server manager)
can see or change them: every button re-checks, since a menu can be pressed minutes
later, and everything here is private to the DM. What the DM says counts as confirmed.
"""

from __future__ import annotations

import difflib
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, PROPOSED, REJECTED, Entity, MemoryRuleError, name_key
from dmbot.memory.sounds import sound_codes
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
    VIEW_TIMEOUT_S,
    _bot,
    _Button,
    _is_manager,
    _Menu,
    _replace,
    _Select,
    _send,
    _tell,
    dmbot_group,
)

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore
    from dmbot.ui.name_lists import Upload

log = logging.getLogger(__name__)

SHOWN_PER_SECTION = 10
PANEL_MAX = 1900  # under Discord's 2,000 characters
SAME_AS_CHOICES = 25  # Discord's limit for a menu
PC = "player_character"

# What the DM can say a name is, in plain words. Keys are the memory rules' kinds.
KINDS: dict[str, str] = {
    "npc": "Someone you play (NPC)",
    PC: "A player's character",
    "place": "A place",
    "faction": "A group (guild, cult, family…)",
    "creature": "A creature or monster",
    "item": "An item",
    "deity": "A god or other power",
    "spell": "A spell or magic",
    "event": "Something that happened (event)",
    "concept": "Something else",
}
KIND_SHORT: dict[str, str] = {
    "npc": "NPC",
    PC: "player's character",
    "character": "character",
    "place": "place",
    "faction": "group",
    "creature": "creature",
    "item": "item",
    "deity": "god",
    "spell": "spell",
    "event": "event",
    "concept": "other",
}
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
GONE = "That name was just changed or removed. Run `/dmbot names` to try again."
# Editing and removing live on each name's card; say how to get there (#353).
EDIT_HINT = (
    "To fix a spelling, change what a name is, or remove it, pick it below or press 🔍 Find a name."
)
MISTAKE_HINT = "Made a mistake? Fix or remove it with the buttons below."


def _memory(interaction: discord.Interaction) -> MemoryStore | None:
    return _bot(interaction).memory


def _md(text: str) -> str:
    """A name as typed or heard, shown as-is (no stray bold or italics)."""
    return discord.utils.escape_markdown(text)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def split_names(raw: str) -> list[str]:
    """'Bell, Bel ; the knight' → ['Bell', 'Bel', 'the knight'] (no repeats, no blanks)."""
    seen: set[str] = set()
    out = []
    for part in re.split(r"[,;\n]", raw):
        text = " ".join(part.split())
        if text and name_key(text) not in seen:
            seen.add(name_key(text))
            out.append(text)
    return out


def sees_secrets(campaign: Campaign, user_id: int) -> bool:
    """Secret names are for the campaign's DMs only: a server manager who isn't one of
    them may be at the table. They never see, search, add or even hear of one."""
    return user_id in campaign.dm_user_ids


def changed(interaction: discord.Interaction, campaign: Campaign) -> None:
    """After DMbot's own change to the names: the next card or search reads fresh."""
    cache = _bot(interaction).lookup
    if cache is not None:
        cache.mark_stale(campaign.guild_id, campaign.id)


async def _campaign_for(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if this person may manage its names; tells them otherwise."""
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return None
    campaign = await _bot(interaction).campaigns.get(guild.id, campaign_id)
    if campaign is None or not logic.can_run(
        campaign, interaction.user.id, _is_manager(interaction)
    ):
        await _tell(interaction, logic.NO_CAMPAIGN_ACCESS)
        return None
    return campaign


def ranked_same_as(heard: str, entities: list[Entity]) -> list[Entity]:
    """Known names most likely to be the same as `heard`: sound first, then spelling."""
    codes = set(sound_codes(heard))
    key = name_key(heard)

    def score(e: Entity) -> tuple[int, float]:
        sounds = bool(codes & set(sound_codes(e.name)))
        return (0 if sounds else 1, -difflib.SequenceMatcher(None, key, name_key(e.name)).ratio())

    return sorted(entities, key=score)[:SAME_AS_CHOICES]


# ---- home ------------------------------------------------------------------------------


def home_text(
    names: CampaignLookup, campaign: Campaign, waiting: int
) -> tuple[str, list[tuple[str, str, str]]]:
    """The overview (about 15 lines, under Discord's limit): how many names of each
    kind, the waiting check, names heard last session and names added lately. Returns
    the text and the names shown, for the "Open a name…" menu. No secret names: those
    are on each name's card, for the campaign's DMs."""
    confirmed = [e for e in names.entities.values() if e.status == CONFIRMED]
    lines = [f"🧠 **Names DMbot knows for {_md(campaign.name)}**"]
    if not confirmed:
        lines.append(
            "None yet. Add the names your table says out loud (characters, NPCs, places). "
            "DMbot listens for them so the transcript spells them right: **Belleros**, not "
            '"Bell Eros". It never makes up story.'
        )
    else:
        kinds: dict[str, int] = {}
        other = 0
        for e in confirmed:
            kind = KIND_SHORT.get(e.type, e.type)
            if kind == "other":
                other += 1
            else:
                kinds[kind] = kinds.get(kind, 0) + 1
        top = sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))
        parts = [
            f"{n} {kind}{'s' if n != 1 and not kind.endswith('s') else ''}" for kind, n in top[:3]
        ]
        rest = sum(n for _, n in top[3:]) + other
        if rest:
            parts.append(f"{rest} other")
        lines.append(f"**{_plural(len(confirmed), 'name')}:** {', '.join(parts)}")
        lines.append(
            "DMbot listens for these names and spells them right. It never makes up story."
        )
    if waiting:
        lines.append(f"📝 **{_plural(waiting, 'new name')} from your sessions to check.**")
    shown: list[tuple[str, str, str]] = []

    def section(title: str, ids: list[str]) -> None:
        taken = {item[0] for item in shown}
        fresh = [e for e in ids if e not in taken]
        if not fresh:
            return
        listed = fresh[:SHOWN_PER_SECTION]
        text = ", ".join(f"**{_md(names.entities[e].name)}**" for e in listed)
        more = f" … and {len(fresh) - len(listed)} more" if len(fresh) > len(listed) else ""
        lines.append(f"{title} {text}{more}")
        shown.extend(
            (e, names.entities[e].name, KIND_SHORT.get(names.entities[e].type, "other"))
            for e in listed
        )

    last = names.recent_sessions[0] if names.recent_sessions else None
    heard = sorted(
        (h for h in names.heard.values() if last is not None and h.last_session_at == last),
        key=lambda h: -h.times,
    )
    ids = {e.id for e in confirmed}
    section("👂 **Heard last session:**", [h.entity_id for h in heard if h.entity_id in ids])
    section("🆕 **Added lately:**", [e.id for e in sorted(confirmed, key=lambda e: -e.created_at)])
    if shown:  # the menu below lists them
        lines.append(EDIT_HINT)
    text = "\n".join(lines)
    return (text if len(text) <= PANEL_MAX else text[: PANEL_MAX - 1] + "…"), shown


class NamesHome(_Menu):
    def __init__(
        self, campaign_id: str, waiting: int, shown: list[tuple[str, str, str]] | None = None
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(
            _Button(self._find, label="🔍 Find a name", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._add, label="➕ Add a name", style=discord.ButtonStyle.success))
        self.add_item(
            _Button(
                self._check,
                label=f"📝 Check new names ({waiting})",
                style=discord.ButtonStyle.primary if waiting else discord.ButtonStyle.secondary,
                disabled=waiting == 0,
            )
        )
        self.add_item(
            _Button(
                self._character,
                label="🧑 Add a player's character",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
        )
        grey = discord.ButtonStyle.secondary
        self.add_item(_Button(self._browse, label="📚 Browse by kind", style=grey, row=1))
        self.add_item(_Button(self._add_many, label="📥 Add many", style=grey, row=1))
        self.add_item(_Button(self._download, label="📤 Download all", style=grey, row=1))
        if shown:
            self.open = _Select(
                self._open,
                placeholder="✏️ Edit or remove a name…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(name, logic.OPTION_LABEL_MAX), value=e, description=kind
                    )
                    for e, name, kind in shown[: logic.SELECT_OPTIONS_MAX]
                ],
                row=2,
            )
            self.add_item(self.open)

    async def _find(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import FindForm

        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(FindForm(self.campaign_id))

    async def _open(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        await show_card(interaction, self.campaign_id, self.open.values[0])

    async def _browse(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_lists import show_browse

        await show_browse(interaction, self.campaign_id)

    async def _add_many(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_lists import AddMany, format_help

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            secrets = sees_secrets(campaign, interaction.user.id)
            await _send(interaction, format_help(secrets=secrets), AddMany(self.campaign_id))

    async def _download(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_lists import send_download

        await send_download(interaction, self.campaign_id)

    async def _add(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            secrets = sees_secrets(campaign, interaction.user.id)
            await interaction.response.send_modal(AddNameForm(self.campaign_id, secrets=secrets))

    async def _character(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            view = PlayerPicker(self.campaign_id)
            await _send(interaction, "**Who plays this character?**", view)

    async def _check(self, interaction: discord.Interaction) -> None:
        await start_review(interaction, self.campaign_id)


async def show_home(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    cache = _bot(interaction).lookup
    if memory is None or cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    try:
        names = await cache.get(campaign.guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't load the campaign's names")
        await _tell(interaction, "DMbot couldn't load the names just now. Try again in a moment.")
        return
    waiting = len(await memory.entities(campaign.guild_id, campaign.id, statuses=[PROPOSED]))
    text, shown = home_text(names, campaign, waiting)
    await _send(interaction, text, NamesHome(campaign.id, waiting, shown))


class CampaignChoice(_Menu):
    def __init__(
        self, campaigns: list[Campaign], find: str | None = None, upload: Upload | None = None
    ) -> None:
        super().__init__()
        self.find = find
        self.upload = upload  # a list of names added with /dmbot names file:
        now = int(time.time())
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(
                    label=logic.name_label(c.name),
                    value=c.id,
                    description=logic.option_description(c, now),
                )
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        if self.upload is not None:
            from dmbot.ui.name_lists import take_list

            await take_list(interaction, self.pick.values[0], self.upload)
        elif self.find:  # the name typed with /dmbot names find:, now the campaign is known
            from dmbot.ui.name_card import open_found

            await open_found(interaction, self.pick.values[0], self.find)
        else:
            await show_home(interaction, self.pick.values[0])


async def _find_typeahead(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from dmbot.ui.name_card import find_typeahead

    return await find_typeahead(interaction, current)


@dmbot_group.command(
    name="names", description="Teach DMbot your campaign's names so it spells them right"
)
@app_commands.describe(
    find="Open one name: type part of it, a nickname, or how it sounds",
    file="Add names from a file: a names list, or a .txt, .pdf or .docx (Word) document",
    link="Add names from a link: a document or web page anyone with the link can open",
)
@app_commands.autocomplete(find=_find_typeahead)
async def dmbot_names(
    interaction: discord.Interaction,
    find: str | None = None,
    file: discord.Attachment | None = None,
    link: str | None = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    upload = None
    if (file is not None or link) and mine:  # only read for someone who may use it
        from dmbot.ui.name_lists import NO_AI_FOR_DOCUMENTS, read_attachment, read_link

        if file is None and bot.ai is None:  # a link is always for the AI: don't fetch it
            await _tell(interaction, NO_AI_FOR_DOCUMENTS)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if file is not None:
            upload, problem = await read_attachment(file)
        else:
            upload, problem = await read_link(link or "")
        if upload is None:
            await _tell(interaction, problem or "DMbot couldn't read that.")
            return
        if find:
            await _tell(interaction, "Adding the list first. Search for a name afterwards.")
    if find and upload is None:
        from dmbot.ui.name_card import open_found, typeahead_campaign

        campaign = await typeahead_campaign(interaction)
        if campaign is not None:
            await open_found(interaction, campaign.id, find)
            return
    playing = bot.active_campaign_id(guild.id)
    only = playing if any(c.id == playing for c in mine) else None
    if only is None and len(mine) == 1:
        only = mine[0].id
    if upload is not None and only is not None:
        from dmbot.ui.name_lists import take_list

        await take_list(interaction, only, upload)
    elif playing is not None and any(c.id == playing for c in mine):
        await show_home(interaction, playing)
    elif not mine:
        await _tell(
            interaction, "You're not the DM of any campaign here. Use `/dmbot start` to set one up."
        )
    elif len(mine) == 1:
        await show_home(interaction, mine[0].id)
    else:
        await _send(interaction, "**Which campaign's names?**", CampaignChoice(mine, find, upload))


# ---- adding a name ---------------------------------------------------------------------


class AddNameForm(discord.ui.Modal, title="Add a name"):
    name: discord.ui.TextInput[AddNameForm] = discord.ui.TextInput(
        label="Name", placeholder="For example: Belleros", max_length=100
    )
    others: discord.ui.TextInput[AddNameForm] = discord.ui.TextInput(
        label="Other names or nicknames (optional)",
        placeholder="Separate with commas. For example: Bell, the old knight",
        required=False,
        max_length=400,
    )
    secret: discord.ui.TextInput[AddNameForm] = discord.ui.TextInput(
        label="Disguises or secret identities (optional)",
        placeholder="Example: the hooded stranger. Players never learn who it really is.",
        required=False,
        max_length=400,
    )

    def __init__(self, campaign_id: str, *, secrets: bool) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        if not secrets:  # only the campaign's DMs deal in secret names
            self.remove_item(self.secret)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        name = " ".join(self.name.value.split())
        # Only names everyone may know about: a secret one is never confirmed here.
        clash = await known_as(memory, campaign, name)
        if clash is not None:
            await _tell(interaction, already_known(name, clash))
            return
        secret = (
            split_names(self.secret.value) if sees_secrets(campaign, interaction.user.id) else []
        )
        view = KindPicker(self.campaign_id, name, split_names(self.others.value), secret)
        await _send(interaction, f"**What is {_md(name)}?**", view)


async def known_as(memory: MemoryStore, campaign: Campaign, name: str) -> str | None:
    """The confirmed entry already called `name` (by a name everyone may know), if any."""
    key = name_key(name)
    for a in await memory.aliases(campaign.guild_id, campaign.id):
        if a.key == key and a.status == CONFIRMED:
            entity = await memory.entity(campaign.guild_id, campaign.id, a.entity_id)
            if entity is not None and entity.status == CONFIRMED:
                return entity.name
    return None


def already_known(name: str, entry: str) -> str:
    if name_key(name) == name_key(entry):
        return f"DMbot already knows **{_md(entry)}**. Open it with 🔍 Find a name."
    return (
        f"DMbot already knows **{_md(name)}** (another name for **{_md(entry)}**). Open it "
        "with 🔍 Find a name."
    )


class KindPicker(_Menu):
    def __init__(self, campaign_id: str, name: str, others: list[str], secret: list[str]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.name = name
        self.others = others
        self.secret = secret
        self.pick = _Select(
            self._picked,
            placeholder="Pick one…",
            options=[
                discord.SelectOption(label=label, value=kind)
                for kind, label in KINDS.items()
                if kind != PC  # those are added with their player
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.stop()
        try:
            entity = await save_name(
                memory, campaign, self.name, self.pick.values[0], self.others, self.secret
            )
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't save that: {exc}", None)
            return
        changed(interaction, campaign)
        # Its card, so fixing or removing it is one press away (#353).
        from dmbot.ui.name_card import show_card

        # The hint first: the card's note is cut to fit, from the end (#353 review).
        note = f"{MISTAKE_HINT}\n{saved_text(entity, self.others, self.secret)}"
        await show_card(interaction, campaign.id, entity.id, replace=True, note=note)


async def save_name(
    memory: MemoryStore,
    campaign: Campaign,
    name: str,
    kind: str,
    others: list[str],
    secret: list[str],
    *,
    played_by: int | None = None,
) -> Entity:
    """Save a name the DM gave, with its other and secret names, as the DM's word."""
    gid, cid = campaign.guild_id, campaign.id
    written = await memory.add_entity(
        gid, cid, type=kind, name=name, source=DM, status=CONFIRMED, played_by=played_by
    )
    entity = written.value
    for other in others:
        await memory.add_alias(
            gid, cid, entity.id, other, kind="nickname", source=DM, status=CONFIRMED
        )
    for hidden in secret:
        await memory.add_alias(
            gid, cid, entity.id, hidden, kind="title", source=DM, status=CONFIRMED, secret=True
        )
    return entity


def saved_text(entity: Entity, others: list[str], secret: list[str]) -> str:
    kind = KIND_SHORT.get(entity.type, entity.type)
    lines = [f"✅ DMbot will remember **{_md(entity.name)}** ({kind})."]
    if others:
        lines.append(f"It also listens for: {', '.join(map(_md, others))}.")
    if secret:
        lines.append(
            f"🤫 Kept secret: **{', '.join(map(_md, secret))}** is really "
            f"**{_md(entity.name)}**. Only you see this. DMbot never puts it in the transcript."
        )
    return "\n".join(lines)


# ---- a player's character --------------------------------------------------------------


class _UserSelect(discord.ui.UserSelect[Any]):
    def __init__(self, on_pick: Any) -> None:
        super().__init__(placeholder="Pick the player…")
        self.on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.on_pick(interaction, self.values[0])


class PlayerPicker(_Menu):
    def __init__(self, campaign_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(_UserSelect(self.picked))

    async def picked(
        self, interaction: discord.Interaction, player: discord.User | discord.Member
    ) -> None:
        if player.bot:
            await _tell(interaction, "Pick a person, not a bot.")
            return
        if await _campaign_for(interaction, self.campaign_id):
            self.stop()
            await interaction.response.send_modal(
                CharacterForm(self.campaign_id, player.id, player.display_name)
            )


class CharacterForm(discord.ui.Modal, title="Add a player's character"):
    name: discord.ui.TextInput[CharacterForm] = discord.ui.TextInput(
        label="Character's name", placeholder="For example: Cerric", max_length=100
    )
    others: discord.ui.TextInput[CharacterForm] = discord.ui.TextInput(
        label="Nicknames (optional)",
        placeholder="Separate with commas. For example: Cer",
        required=False,
        max_length=400,
    )

    def __init__(self, campaign_id: str, player_id: int, player_name: str) -> None:
        super().__init__(
            title=logic.shorten(f"{player_name}'s character", 45), timeout=VIEW_TIMEOUT_S
        )
        self.campaign_id = campaign_id
        self.player_id = player_id
        self.player_name = player_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        others = split_names(self.others.value)
        try:
            entity = await save_name(
                memory, campaign, self.name.value, PC, others, [], played_by=self.player_id
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't save that: {exc}")
            return
        changed(interaction, campaign)
        await _tell(
            interaction,
            f"✅ DMbot will remember **{_md(entity.name)}**, played by "
            f"**{_md(self.player_name)}**."
            + (f" It also listens for: {', '.join(map(_md, others))}." if others else ""),
        )


# ---- checking the names DMbot suggests --------------------------------------------------


def suggestion_text(entity: Entity, left: int) -> str:
    heard = f" ({entity.description.lower()})" if entity.description else ""
    more = f"\n_{_plural(left - 1, 'more name')} after this one._" if left > 1 else ""
    return f"📝 **{_md(entity.name)}**{heard}\nIs this a name in your game?{more}"


async def start_review(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    waiting = await memory.entities(campaign.guild_id, campaign.id, statuses=[PROPOSED])
    if not waiting:
        await _tell(interaction, "✅ No new names to check. DMbot suggests some after a session.")
        return
    waiting.sort(key=lambda e: (e.created_at, e.name))
    view = SuggestionReview(campaign.id, [e.id for e in waiting])
    await _send(interaction, suggestion_text(waiting[0], len(waiting)), view)


class SuggestionReview(_Menu):
    """One suggested name at a time: Yes (then what it is), Same as…, Not a name, Later."""

    def __init__(self, campaign_id: str, entity_ids: list[str]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.queue = entity_ids
        self.later = 0  # left for another time this round
        self._buttons()

    def _buttons(self) -> None:
        self.clear_items()
        self.add_item(_Button(self._yes, label="✅ Yes, add it", style=discord.ButtonStyle.success))
        self.add_item(
            _Button(self._same, label="🔗 Same as a known name…", style=discord.ButtonStyle.primary)
        )
        self.add_item(_Button(self._no, label="🚫 Not a name", style=discord.ButtonStyle.secondary))
        self.add_item(_Button(self._later, label="Later", style=discord.ButtonStyle.secondary))

    async def _current(
        self, interaction: discord.Interaction
    ) -> tuple[Campaign, MemoryStore, Entity] | None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return None
        if not self.queue:
            await self._finish(interaction, None)
            return None
        entity = await memory.entity(campaign.guild_id, campaign.id, self.queue[0])
        if entity is None or entity.status != PROPOSED:
            self.queue.pop(0)
            await self._next(interaction, campaign, memory, note=None)
            return None
        return campaign, memory, entity

    async def _next(
        self,
        interaction: discord.Interaction,
        campaign: Campaign,
        memory: MemoryStore,
        *,
        note: str | None,
    ) -> None:
        """Show the next suggestion still waiting, or say we're done."""
        while self.queue:
            entity = await memory.entity(campaign.guild_id, campaign.id, self.queue[0])
            if entity is not None and entity.status == PROPOSED:
                self._buttons()
                text = suggestion_text(entity, len(self.queue))
                await _replace(interaction, f"{note}\n\n{text}" if note else text, self)
                return
            self.queue.pop(0)
        await self._finish(interaction, note)

    async def _finish(self, interaction: discord.Interaction, note: str | None) -> None:
        self.stop()
        if self.later:
            done = (
                f"Done for now. {_plural(self.later, 'name')} still waiting: run "
                "`/dmbot names` to check them later."
            )
        else:
            done = "✅ All checked. DMbot will listen for the names you said yes to."
        await _replace(interaction, f"{note}\n\n{done}" if note else done, None)

    async def _saved(
        self,
        interaction: discord.Interaction,
        campaign: Campaign,
        memory: MemoryStore,
        note: str,
    ) -> None:
        self.queue.pop(0)
        await self._next(interaction, campaign, memory, note=note)

    async def _yes(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        _, _, entity = current
        self.clear_items()
        self.kind = _Select(
            self._kind_picked,
            placeholder=logic.shorten(f"What is {entity.name}?", 150),
            options=[
                discord.SelectOption(label=label, value=kind) for kind, label in KINDS.items()
            ],
        )
        self.add_item(self.kind)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
        await _replace(interaction, f"**What is {_md(entity.name)}?**", self)

    async def _kind_picked(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        kind = self.kind.values[0]
        if kind == PC:  # who plays them?
            self.clear_items()
            self.add_item(_UserSelect(self._player_picked))
            self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
            await _replace(interaction, f"**Who plays {_md(entity.name)}?**", self)
            return
        try:
            await memory.confirm_entity(campaign.guild_id, campaign.id, entity.id, kind, source=DM)
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        await self._saved(interaction, campaign, memory, f"✅ Added **{_md(entity.name)}**.")

    async def _player_picked(
        self, interaction: discord.Interaction, player: discord.User | discord.Member
    ) -> None:
        if player.bot:
            await _tell(interaction, "Pick a person, not a bot.")
            return
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        try:
            await memory.confirm_entity(
                campaign.guild_id, campaign.id, entity.id, PC, source=DM, played_by=player.id
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        note = f"✅ Added **{_md(entity.name)}**, played by **{_md(player.display_name)}**."
        await self._saved(interaction, campaign, memory, note)

    async def _same(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        known = await memory.entities(campaign.guild_id, campaign.id, statuses=[CONFIRMED])
        if not known:
            await _tell(
                interaction, "DMbot doesn't know any names yet. Press ✅ Yes, add it instead."
            )
            return
        self.clear_items()
        self.same = _Select(
            self._same_picked,
            placeholder=logic.shorten(f"{entity.name} is the same as…", 150),
            options=[
                discord.SelectOption(
                    label=logic.shorten(e.name, logic.OPTION_LABEL_MAX),
                    value=e.id,
                    description=KIND_SHORT.get(e.type, e.type),
                )
                for e in ranked_same_as(entity.name, known)
            ],
        )
        self.add_item(self.same)
        self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
        await _replace(
            interaction,
            f"Which name is **{_md(entity.name)}** the same as? Not in the list? Press Back.",
            self,
        )

    async def _same_picked(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        gid, cid = campaign.guild_id, campaign.id
        keep = await memory.entity(gid, cid, self.same.values[0])
        if keep is None or keep.status != CONFIRMED:
            await _tell(interaction, GONE)
            return
        try:
            await memory.merge(gid, cid, keep.id, entity.id, source=DM, dm_said_same=True)
            heard = name_key(entity.name)
            for alias in await memory.aliases(gid, cid, entity_id=keep.id, include_secret=True):
                if alias.key == heard and alias.status != CONFIRMED:
                    await memory.update_alias(gid, cid, alias.id, status=CONFIRMED, source=DM)
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        note = f"🔗 Got it: **{_md(entity.name)}** is another name for **{_md(keep.name)}**."
        await self._saved(interaction, campaign, memory, note)

    async def _no(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        try:
            await memory.set_entity_status(
                campaign.guild_id, campaign.id, entity.id, REJECTED, source=DM
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        note = f"🚫 OK, **{_md(entity.name)}** isn't a name. DMbot won't suggest it again."
        await self._saved(interaction, campaign, memory, note)

    async def _later(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, _ = current
        self.later += 1
        self.queue.pop(0)  # this round only; it's still waiting next time
        await self._next(interaction, campaign, memory, note=None)

    async def _back(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, _ = current
        await self._next(interaction, campaign, memory, note=None)


class ReviewButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:review:(?P<campaign>[0-9a-f]{32})",
):
    """On the DM screen after a session: opens the check privately for whoever runs the
    campaign. Works after a restart (the campaign is in the button's ID)."""

    def __init__(self, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="📝 Check new names",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:names:review:{campaign_id}",
            )
        )
        self.campaign_id = campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> ReviewButton:
        return cls(match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        await start_review(interaction, self.campaign_id)


def review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(campaign_id))
    return view


def after_session_text(names: list[str]) -> str:
    """For the DM screen (players may see it under peek; the names come from the
    transcript anyone can read, so nothing is spoiled)."""
    shown = ", ".join(f"**{_md(n)}**" for n in names[:5])
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    return (
        f"📝 **{_plural(len(names), 'new name')} to check** from this session: {shown}{more}.\n"
        "Say which are in your game, so DMbot spells them right. The check opens privately "
        "for the DM."
    )
