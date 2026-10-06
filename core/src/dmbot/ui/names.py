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

from dmbot.campaigns import Campaign
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

log = logging.getLogger(__name__)

SHOWN_NAMES = 15
SHOWN_OTHER_NAMES = 3
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


async def home_text(memory: MemoryStore, campaign: Campaign) -> tuple[str, int]:
    """The panel: names DMbot knows (with other and secret names; it's private to the
    DM), kept under Discord's message limit."""
    gid, cid = campaign.guild_id, campaign.id
    confirmed = await memory.entities(gid, cid, statuses=[CONFIRMED])
    waiting = await memory.entities(gid, cid, statuses=[PROPOSED])
    aliases = await memory.aliases(gid, cid, include_secret=True)
    others: dict[str, list[str]] = {}
    secrets: dict[str, list[str]] = {}
    by_id = {e.id: e for e in confirmed}
    for a in aliases:
        entity = by_id.get(a.entity_id)
        if entity is None or a.key == name_key(entity.name) or a.status != CONFIRMED:
            continue
        (secrets if a.secret else others).setdefault(a.entity_id, []).append(_md(a.text))
    lines = [f"🧠 **Names DMbot knows for {_md(campaign.name)}**"]
    if confirmed:
        lines.append(
            "DMbot listens for these names and spells them right in the transcript. "
            "It never makes up story."
        )
    else:
        lines.append(
            "None yet. Add the names your table says out loud (characters, NPCs, places). "
            "DMbot listens for them so the transcript spells them right: **Belleros**, not "
            '"Bell Eros". It never makes up story.'
        )
    if waiting:
        lines.append(f"📝 **{_plural(len(waiting), 'new name')} from your sessions to check.**")
    shown = 0
    for e in sorted(confirmed, key=lambda e: -e.created_at)[:SHOWN_NAMES]:
        line = f"• **{_md(e.name)}**, {KIND_SHORT.get(e.type, e.type)}"
        also = others.get(e.id, [])
        if also:
            extra = (
                f" +{len(also) - SHOWN_OTHER_NAMES} more" if len(also) > SHOWN_OTHER_NAMES else ""
            )
            line += f" (also: {', '.join(also[:SHOWN_OTHER_NAMES])}{extra})"
        if secrets.get(e.id):
            line += f" 🤫 secret: {', '.join(secrets[e.id][:SHOWN_OTHER_NAMES])}"
        if len("\n".join([*lines, line])) > PANEL_MAX - 40:
            break
        lines.append(line)
        shown += 1
    if len(confirmed) > shown:
        lines.append(f"…and {len(confirmed) - shown} more.")
    return "\n".join(lines), len(waiting)


class NamesHome(_Menu):
    def __init__(self, campaign_id: str, waiting: int) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(_Button(self._add, label="➕ Add a name", style=discord.ButtonStyle.success))
        self.add_item(
            _Button(
                self._character,
                label="🧑 Add a player's character",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(
            _Button(
                self._check,
                label=f"📝 Check new names ({waiting})",
                style=discord.ButtonStyle.secondary,
                disabled=waiting == 0,
            )
        )

    async def _add(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(AddNameForm(self.campaign_id))

    async def _character(self, interaction: discord.Interaction) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            view = PlayerPicker(self.campaign_id)
            await _send(interaction, "**Who plays this character?**", view)

    async def _check(self, interaction: discord.Interaction) -> None:
        await start_review(interaction, self.campaign_id)


async def show_home(interaction: discord.Interaction, campaign_id: str) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    text, waiting = await home_text(memory, campaign)
    await _send(interaction, text, NamesHome(campaign.id, waiting))


class CampaignChoice(_Menu):
    def __init__(self, campaigns: list[Campaign]) -> None:
        super().__init__()
        now = int(time.time())
        self.pick = _Select(
            self._picked,
            placeholder="Which campaign?",
            options=[
                discord.SelectOption(
                    label=logic.shorten(c.name, logic.OPTION_LABEL_MAX),
                    value=c.id,
                    description=logic.option_description(c, now),
                )
                for c in campaigns[: logic.SELECT_OPTIONS_MAX]
            ],
        )
        self.add_item(self.pick)

    async def _picked(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_home(interaction, self.pick.values[0])


@dmbot_group.command(
    name="names", description="Teach DMbot your campaign's names so it spells them right"
)
async def dmbot_names(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await _tell(interaction, NOT_IN_SERVER)
        return
    bot = _bot(interaction)
    mine = logic.runnable(
        await bot.campaigns.list_campaigns(guild.id), interaction.user.id, _is_manager(interaction)
    )
    playing = bot.active_campaign_id(guild.id)
    if playing is not None and any(c.id == playing for c in mine):
        await show_home(interaction, playing)
    elif not mine:
        await _tell(
            interaction, "You're not the DM of any campaign here. Use `/dmbot start` to set one up."
        )
    elif len(mine) == 1:
        await show_home(interaction, mine[0].id)
    else:
        await _send(interaction, "**Which campaign's names?**", CampaignChoice(mine))


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

    def __init__(self, campaign_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        name = " ".join(self.name.value.split())
        known = await memory.aliases(campaign.guild_id, campaign.id, include_secret=True)
        if any(a.key == name_key(name) for a in known):
            await _tell(interaction, f"DMbot already knows **{_md(name)}**.")
            return
        view = KindPicker(
            self.campaign_id, name, split_names(self.others.value), split_names(self.secret.value)
        )
        await _send(interaction, f"**What is {_md(name)}?**", view)


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
        await _replace(interaction, saved_text(entity, self.others, self.secret), None)


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
