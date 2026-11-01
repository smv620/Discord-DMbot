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
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import discord
from discord import app_commands

from dmbot.campaigns import Campaign
from dmbot.discord_limits import MESSAGE_MAX, fit_message
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import (
    BACKGROUND_SOURCE,
    CONFIRMED,
    DM,
    NAME_MAX,
    PROPOSED,
    REJECTED,
    Entity,
    MemoryRuleError,
    name_key,
)
from dmbot.memory.name_longer import longer_names, widens
from dmbot.memory.scan import Match, near_match_in, sound_keys
from dmbot.memory.sounds import sound_codes
from dmbot.ui import all_names, logic, name_places, player_id
from dmbot.ui.dmbot_commands import (
    NOT_IN_SERVER,
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
    dmbot_group,
)

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore
    from dmbot.ui.name_lists import Upload

log = logging.getLogger(__name__)

SHOWN_PER_SECTION = 10
PANEL_MAX = MESSAGE_MAX - 100  # margin kept from before: room for the words a sender adds around it
SAME_AS_CHOICES = 25  # Discord's limit for a menu
PC = "player_character"

# What the DM can say a name is, in plain words. Keys are the memory rules' kinds.
KINDS: dict[str, str] = {
    "npc": "NPC (the DM plays them)",
    PC: "PC (a player's character)",
    "place": "A place",
    "faction": "A group (guild, cult)",
    "item": "An item",
    "event": "An event",
    "clue": "A clue or secret",
    "people": "A people (elves…)",
    "quest": "A quest",
    "mystery": "A mystery",
    "hook": "A hook (a lead)",
    "goal": "A goal",
    "promise": "A promise",
    "concept": "An idea or other thing",
}
KIND_SHORT: dict[str, str] = {
    "npc": "NPC",
    PC: "PC",
    "character": "character",
    "place": "place",
    "faction": "group",
    "creature": "NPC",
    "item": "item",
    "deity": "NPC",
    "spell": "spell",
    "event": "event",
    "clue": "clue",
    "people": "people",
    "thread": "thread",
    "quest": "quest",
    "mystery": "mystery",
    "hook": "hook",
    "goal": "goal",
    "promise": "promise",
    "concept": "other",
    "person": "player or DM",
}
NOT_AVAILABLE = (
    "Remembering names isn't switched on for this DMbot yet. Ask whoever runs DMbot to turn it on."
)
GONE = "That name was just changed or removed. Run `/dmbot names` to try again."
# In the review, where the next suggestion is already on screen (#372 review).
NOT_JOINED = "That name was just changed or removed, so they weren't joined. Here's the next one."
# Editing and removing live on each name's card; say how to get there (#353).
EDIT_HINT = "Fix, change or remove a name: pick it below, or press 🔍 Find a name."
MISTAKE_HINT = "Wrong? Fix or remove it with the buttons below."


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


# The server of a campaign whose cards were opened from a private message (#2977): a private message
# has no server, so the card's buttons would all answer "Use this in a server". Only the campaign's
# own DM (checked by `can_run` when the card is opened) puts a campaign here, and every press is
# checked again below. Lost on a restart: a card from before it asks to be opened again.
_PRIVATE_SERVERS: dict[str, int] = {}


async def _campaign_for(interaction: discord.Interaction, campaign_id: str) -> Campaign | None:
    """The campaign, if this person may manage its names; tells them otherwise."""
    guild_id = interaction.guild.id if interaction.guild is not None else None
    if guild_id is None:
        guild_id = _PRIVATE_SERVERS.get(campaign_id)
    if guild_id is None:
        # Not remembered (a restart, or a question sent to the DM's private chat, #3210): the
        # database says which server the campaign is in, so old buttons keep working.
        guild_id = await _bot(interaction).campaigns.guild_of(campaign_id)
    if guild_id is None:
        await _tell(interaction, NOT_IN_SERVER)
        return None
    campaign = await _bot(interaction).campaigns.get(guild_id, campaign_id)
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


@dataclass(frozen=True, slots=True)
class Reviews:
    """How many answers of each kind wait for the DM, besides the new names (#1986). The same
    reviews the after-session posts open, counted again so a DM who missed the post can still
    answer. Counts only: nothing here says what was said or by whom."""

    heard: int = 0  # ❓ "Did they mean…?"
    people: int = 0  # 🧑 who is who
    claims: int = 0  # 🗣️ what was said (only when the story extractor is on)
    said: int = 0  # 🔊 how the narrator should say a name (#2171)


def review_line(reviews: Reviews) -> str | None:
    """One plain line for the waiting reviews, or None when nothing waits."""
    parts = [
        f"{_plural(reviews.heard, 'name')} to double-check" if reviews.heard else "",
        f"{_plural(reviews.people, 'question')} about who is who" if reviews.people else "",
        f"{_plural(reviews.claims, 'thing')} said to check" if reviews.claims else "",
        f"{_plural(reviews.said, 'name')} to say out loud" if reviews.said else "",
    ]
    parts = [p for p in parts if p]
    return f"⏳ **Waiting for you:** {', '.join(parts)}." if parts else None


def home_text(
    names: CampaignLookup, campaign: Campaign, waiting: int, reviews: Reviews | None = None
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
            kind = KIND_SHORT.get(e.kind_key, e.kind_key)
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
    review = review_line(reviews) if reviews else None
    if review:
        lines.append(review)
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
            (e, names.entities[e].name, KIND_SHORT.get(names.entities[e].kind_key, "other"))
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
    return fit_message(text, limit=PANEL_MAX), shown


class NamesHome(_Menu):
    def __init__(
        self,
        campaign_id: str,
        waiting: int,
        shown: list[tuple[str, str, str]] | None = None,
        reviews: Reviews | None = None,
        left_out: bool = False,
        connections: int = 0,
        look_alikes: int | None = None,
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
        if look_alikes is not None:  # the DM's: names that may be one name (#2426)
            from dmbot.ui.look_alikes import label as look_alikes_label

            self.add_item(
                _Button(self._look_alikes, label=look_alikes_label(look_alikes), style=grey, row=1)
            )
        self.add_item(_Button(self._new_thread, label="🧵 New thread", style=grey, row=3))
        self.add_item(_Button(self._new_promise, label="🤝 Add a promise", style=grey, row=3))
        # Players' D&D Beyond sheets (#723): read again now, not only at /dmbot start.
        self.add_item(_Button(self._sheets, label="📜 Refresh sheets", style=grey, row=3))
        # The reviews the after-session posts open, one button each, only while something waits
        # (#1986). Row 4 is free: the other rows hold the menu's buttons and the name picker.
        if reviews:
            for count, label, handler in (
                (reviews.heard, "❓ Check names", self._check_heard),
                (reviews.people, "🧑 Check who is who", self._check_people),
                (reviews.claims, "🗣️ Check what was said", self._check_claims),
                (reviews.said, "🔊 Check how to say", self._check_said),
            ):
                if count:
                    shown_count = str(count) if count < 100 else "99+"  # keeps the label short
                    self.add_item(
                        _Button(handler, label=f"{label} ({shown_count})", style=grey, row=4)
                    )
        if connections:  # a backstory's connections still wait for the DM (#2380)
            shown_links = str(connections) if connections < 100 else "99+"
            self.add_item(
                _Button(
                    self._connections,
                    label=f"📜 Check the connections ({shown_links})",
                    style=grey,
                    row=4,
                )
            )
        if shown:
            self.open = _Select(
                self._open,
                placeholder="✏️ Fix, change or remove a name…",
                options=all_names.with_choice(
                    [
                        discord.SelectOption(
                            label=logic.shorten(name, logic.OPTION_LABEL_MAX),
                            value=e,
                            description=kind,
                        )
                        for e, name, kind in shown
                    ],
                    left_out=left_out,
                ),
                row=2,
            )
            self.add_item(self.open)

    async def _new_thread(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import NewThreadForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can start a story thread.")
            return
        await interaction.response.send_modal(NewThreadForm(self.campaign_id))

    async def _new_promise(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.threads import NewPromiseForm

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can add a promise.")
            return
        await interaction.response.send_modal(NewPromiseForm(self.campaign_id))

    async def _find(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import FindForm

        if await _campaign_for(interaction, self.campaign_id):
            await interaction.response.send_modal(FindForm(self.campaign_id))

    async def _open(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_card import show_card

        if all_names.chosen(self.open.values):
            await all_names.reply(interaction, self.campaign_id)
            return
        await show_card(interaction, self.campaign_id, self.open.values[0])

    async def _look_alikes(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.look_alikes import show_look_alikes

        await show_look_alikes(interaction, self.campaign_id)

    async def _connections(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.name_background import ask_connections

        await ask_connections(interaction, self.campaign_id)

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
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            view = PlayerPicker(self.campaign_id, campaign.guild_id)
            await _send(interaction, "**Who plays this character?**", view)

    async def _check(self, interaction: discord.Interaction) -> None:
        await start_review(interaction, self.campaign_id)

    async def _check_heard(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.heard_review import start_heard_review

        await start_heard_review(interaction, self.campaign_id)

    async def _check_people(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.people_review import start_people_review

        await start_people_review(interaction, self.campaign_id)

    async def _check_said(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.said_as_review import start_said_as_review

        await start_said_as_review(interaction, self.campaign_id)

    async def _check_claims(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.claims_review import start_claims_review

        await start_claims_review(interaction, self.campaign_id)

    async def _sheets(self, interaction: discord.Interaction) -> None:
        from dmbot.ui.sheets import refresh_all

        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign:
            await refresh_all(interaction, campaign.id, campaign.guild_id)


async def waiting_reviews(interaction: discord.Interaction, campaign: Campaign) -> Reviews:
    """The reviews waiting for the DM, counted as the posts after a session count them. A count
    that can't be read is left out (zero), never an error on the home card."""
    bot: Any = _bot(interaction)
    guild_id = campaign.guild_id
    heard = people = claims = said = 0
    try:
        if bot.heard_questions is not None:
            heard = await bot.heard_questions.count(guild_id, campaign.id)
    except Exception:
        log.exception("Couldn't count the names waiting to be checked")
    memory = _memory(interaction)
    try:
        if memory is not None:
            people = len(await memory.people_suggestions(guild_id, campaign.id))
    except Exception:
        log.exception("Couldn't count the who-is-who questions")
    try:
        if memory is not None and bot.extractor_ai is not None:  # the switch is on
            from dmbot.memory.claims import ClaimStore

            agreed = await bot.consent.consenting(guild_id)
            claims = await ClaimStore(memory).waiting_for_check(
                guild_id, campaign.id, opted_in=agreed
            )
    except Exception:
        log.exception("Couldn't count the things said at the table")
    try:
        if memory is not None and sees_secrets(campaign, interaction.user.id):  # DMs only
            said = len(await memory.said_as_suggestions(guild_id, campaign.id))
    except Exception:
        log.exception("Couldn't count the ways to say names")
    return Reviews(heard=heard, people=people, claims=claims, said=said)


async def show_home(interaction: discord.Interaction, campaign_id: str) -> None:
    await _answer_first(interaction)  # a big campaign's names take a while to load (#537)
    memory = _memory(interaction)
    cache = _bot(interaction).lookup
    if memory is None or cache is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    try:
        names = (await cache.get(campaign.guild_id, campaign.id)).for_viewer(
            sees_secrets(campaign, interaction.user.id)
        )
    except Exception:
        log.exception("Couldn't load the campaign's names")
        await _tell(interaction, "DMbot couldn't load the names just now. Try again in a moment.")
        return
    waiting = len(await memory.entities(campaign.guild_id, campaign.id, statuses=[PROPOSED]))
    reviews = (
        await waiting_reviews(interaction, campaign)
        if sees_secrets(campaign, interaction.user.id)
        else None
    )
    links = getattr(_bot(interaction), "backstory_links", None)
    connections = 0
    if links is not None and sees_secrets(campaign, interaction.user.id):
        try:
            connections = await links.count(campaign.guild_id, campaign.id)
        except Exception:
            log.exception("Couldn't count the waiting connections")
        try:  # other names an AI read suggested wait behind the same button (#3034)
            connections += len(await memory.suggested_names(campaign.guild_id, campaign.id))
        except Exception:
            log.exception("Couldn't count the suggested names")
        try:  # and unnamed people who may be a named one (#3037)
            connections += len(await memory.same_person_offers(campaign.guild_id, campaign.id))
        except Exception:
            log.exception("Couldn't count the possible matches")
        try:  # so do family links worked out from the DM's own (#2960)
            await memory.rework_family(campaign.guild_id, campaign.id)
            connections += len(await memory.worked_out_waiting(campaign.guild_id, campaign.id))
        except Exception:
            log.exception("Couldn't count the worked-out family links")
    look_alikes = None
    if memory is not None and sees_secrets(campaign, interaction.user.id):  # DMs only (#2426)
        from dmbot.ui.look_alikes import count_groups

        look_alikes = await count_groups(interaction, campaign.id)
    text, shown = home_text(names, campaign, waiting, reviews)
    known = sum(1 for e in names.entities.values() if e.status == CONFIRMED)
    await _send(
        interaction,
        text,
        NamesHome(
            campaign.id,
            waiting,
            shown,
            reviews,
            left_out=known > len(shown),
            connections=connections,
            look_alikes=look_alikes,
        ),
    )


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
    file="Add names from a file: a names list, or a PDF, Word, text or web page file",
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
        from dmbot.ui.name_lists import NO_AI_FOR_DOCUMENTS, read_attachment, read_link_once

        if file is None and bot.background_ai is None:  # a link is always for the AI: no fetch
            await _tell(interaction, NO_AI_FOR_DOCUMENTS)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if file is not None:
            upload, problem = await read_attachment(file)
        else:
            upload, problem = await read_link_once(guild.id, link or "")
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

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)  # (#1803) the campaign and name reads come after
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
        view = KindPicker(
            self.campaign_id,
            name,
            split_names(self.others.value),
            secret,
            can_hide=sees_secrets(campaign, interaction.user.id),
        )
        await _send(interaction, view.prompt(), view)


async def known_as(memory: MemoryStore, campaign: Campaign, name: str) -> str | None:
    """The confirmed entry already called `name` (by a name everyone may know), if any."""
    return await memory.confirmed_name_for(campaign.guild_id, campaign.id, name_key(name))


def already_known(name: str, entry: str) -> str:
    if name_key(name) == name_key(entry):
        return f"DMbot already knows **{_md(entry)}**. Open it with 🔍 Find a name."
    return (
        f"DMbot already knows **{_md(name)}** (another name for **{_md(entry)}**). Open it "
        "with 🔍 Find a name."
    )


# A state the DM flips on the kind step (#2321), so the form needs no switch (not every client
# draws one). Each label says what pressing does, as on the name card; no colon, the site's
# button check refuses one.
PLAYERS_KNOW = "🙈 Hide from players"
PLAYERS_DONT_KNOW = "👁️ Show to players"


class KindPicker(_Menu):
    def __init__(
        self,
        campaign_id: str,
        name: str,
        others: list[str],
        secret: list[str],
        *,
        can_hide: bool = False,
    ) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.name = name
        self.others = others
        self.secret = secret
        # A name the DM adds by hand starts unrevealed (#2404): the players meet it when it is
        # said at the table, or when the DM shows it.
        self.hidden = can_hide
        if can_hide:  # only a DM keeps an entry back from the players
            self.hide = _Button(
                self._flip_hidden,
                label=PLAYERS_DONT_KNOW,
                style=discord.ButtonStyle.success,
                row=1,
            )
            self.add_item(self.hide)
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

    def prompt(self) -> str:
        """The question, and (to a DM) whether the name will be kept from the players."""
        text = f"**What is {_md(self.name)}?** Pick one to save it."
        if self.hidden:
            text += (
                "\n🙈 Hidden from players for now. Press Show to players when they should know it."
            )
        return text

    async def _flip_hidden(self, interaction: discord.Interaction) -> None:
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        if not sees_secrets(campaign, interaction.user.id):  # checked on every press
            await _tell(interaction, "Only the DM can change that.")
            return
        self.hidden = not self.hidden
        self.hide.label = PLAYERS_DONT_KNOW if self.hidden else PLAYERS_KNOW
        self.hide.style = (
            discord.ButtonStyle.success if self.hidden else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(content=self.prompt(), view=self)

    async def _picked(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)  # saves, then reloads names
        campaign = await _campaign_for(interaction, self.campaign_id)
        memory = _memory(interaction)
        if campaign is None or memory is None:
            return
        self.stop()
        try:
            entity = await save_name(
                memory, campaign, self.name, self.pick.values[0], self.others, self.secret,
                hidden=self.hidden and sees_secrets(campaign, interaction.user.id),
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _replace(interaction, f"Couldn't save that: {exc}", None)
            return
        changed(interaction, campaign)
        # Its card, so fixing or removing it is one press away (#353).
        from dmbot.ui.name_card import show_card

        # What was saved, then the hint, then the rest: the card's note is cut to fit,
        # from the end (#353, #393).
        saved, *rest = saved_text(
            entity,
            self.others,
            self.secret,
            hidden=self.hidden and sees_secrets(campaign, interaction.user.id),
        ).split("\n")
        note = "\n".join([saved, MISTAKE_HINT, *rest])
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
    hidden: bool = False,
) -> Entity:
    """Save a name the DM gave, with its other and secret names, as the DM's word. `hidden`:
    the players don't know it yet (#2321)."""
    gid, cid = campaign.guild_id, campaign.id
    written = await memory.add_entity(
        gid, cid, type=kind, name=name, source=DM, status=CONFIRMED, played_by=played_by,
        hidden_from_players=hidden,
    )  # fmt: skip
    entity = written.value
    for other in others:
        await memory.add_alias(
            gid, cid, entity.id, other, kind="nickname", source=DM, status=CONFIRMED
        )
    for disguise in secret:
        await memory.add_alias(
            gid, cid, entity.id, disguise, kind="title", source=DM, status=CONFIRMED, secret=True
        )
    return entity


def saved_text(
    entity: Entity, others: list[str], secret: list[str], *, hidden: bool = False
) -> str:
    """What was saved. `hidden`: the DM asked for it hidden from the players (#2321). It is the DM's
    choice just made, not read back from the entry: who may see an entry is the lists' business
    (#2995), so this text never looks at a flag."""
    kind = KIND_SHORT.get(entity.kind_key, entity.kind_key)
    lines = [f"✅ DMbot will remember **{_md(entity.name)}** ({kind})."]
    if hidden:
        lines.append("🙈 Hidden from players. Only you see it.")
    if others:
        lines.append(f"DMbot also listens for: {', '.join(map(_md, others))}.")
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
    def __init__(self, campaign_id: str, guild_id: int | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.add_item(_UserSelect(self.picked))
        if guild_id is not None:  # (#3004) a player who has not joined the server yet
            self.add_item(player_id.type_id_button(guild_id, self._allowed, self._typed))

    async def _allowed(self, interaction: discord.Interaction) -> bool:
        return await _campaign_for(interaction, self.campaign_id) is not None

    async def _typed(self, interaction: discord.Interaction, user_id: int, name: str) -> None:
        if await _campaign_for(interaction, self.campaign_id):
            self.stop()
            await interaction.response.send_modal(CharacterForm(self.campaign_id, user_id, name))

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
    sheet: discord.ui.TextInput[CharacterForm] = discord.ui.TextInput(
        label="D&D Beyond link (optional)",
        placeholder="https://www.dndbeyond.com/characters/12345678",
        required=False,
        max_length=200,
    )

    def __init__(self, campaign_id: str, player_discord_id: int, player_name: str) -> None:
        super().__init__(
            title=logic.shorten(f"{player_name}'s character", 45), timeout=VIEW_TIMEOUT_S
        )
        self.campaign_id = campaign_id
        self.player_id = player_discord_id
        self.player_name = player_name

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction)  # (#1803) the save, and reading a sheet, come after
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
        sheet_note = ""
        if self.sheet.value.strip():
            from dmbot.ui.sheets import dm_link

            sheet_note = "\n" + await dm_link(
                interaction, campaign.guild_id, campaign.id, entity.id, self.sheet.value
            )
        await _tell(
            interaction,
            f"✅ DMbot will remember **{_md(entity.name)}**, played by "
            f"**{_md(self.player_name)}**."
            + (f" It also listens for: {', '.join(map(_md, others))}." if others else "")
            + sheet_note,
        )


# ---- checking the names DMbot suggests --------------------------------------------------


SPELLED_OUT = "Spelled out at the table"  # a suggestion's note when someone spelled it (#2168)
REVIEW_LABEL_MAX = 25  # review buttons fit a phone (#394)


def _heard(entity: Entity) -> str:
    """ " · heard 3 times", from what the scan saved (only a count, never a name)."""
    text = " ".join(entity.description.split())
    return f" · {_md(text[:1].lower() + text[1:])}" if text else ""


def background_kind(entity: Entity) -> str:
    """The kind a suggestion was given when it was read (npc, place…), or "" when it has none to
    confirm as (an idea, or nothing guessed). From the DM's background (#2167) or from the name
    scan, whose AI guesses a kind from the sentence (#2937)."""
    if entity.source not in (BACKGROUND_SOURCE, "scan"):  # marked by where it came from
        return ""
    if entity.type == "character":
        return "npc" if entity.role == "npc" else ""
    return entity.type if entity.type in ("place", "faction", "item", "event") else ""


def a_kind(kind: str) -> str:
    """ "a place", "an item": the kind with its article."""
    word = kind_label(kind)
    an = word.casefold().startswith(
        ("a", "e", "i", "o", "u", "npc")
    )  # ("an NPC": said "en-pee-see")
    return f"{'an' if an else 'a'} {word}"


def kind_label(kind: str) -> str:
    """ "person", "place"…: a kind in plain words (the same ones the background reader uses)."""
    from dmbot.memory.background import LABELS

    return LABELS.get(kind, (kind, kind))[0]


def _bold_kind(kind: str) -> str:
    article, _, word = a_kind(kind).partition(" ")
    return f"{article} **{word}**"


def spelled_fix(entity: Entity) -> str:
    """How someone spelled a pending suggestion at the table, when that differs from how it was
    heard (the note reads "Spelled out at the table: Brannoch"), else ""."""
    lead = f"{SPELLED_OUT}: "
    if not entity.description.startswith(lead):
        return ""
    spelling = entity.description[len(lead) :].strip()
    return spelling if name_key(spelling) != name_key(entity.name) else ""


def suggestion_text(
    entity: Entity,
    left: int,
    *,
    also: list[str] | None = None,
    match: Match | None = None,
    kind: str = "",
    spelled: bool = False,
    fix_to: str = "",
    places: str = "",
    context: str = "",
) -> str:
    # A name read from the DM's writing was read, not heard.
    verb = "written" if entity.source == BACKGROUND_SOURCE else "heard"
    others = (
        f"\nAlso {verb} as {', '.join(f'**{_md(a)}**' for a in also)}: saved with it."
        if also
        else ""
    )
    more = f"\n_{_plural(left - 1, 'more name')} after this one._" if left > 1 else ""
    # Someone spelled it out (#2168): the question says what the extra button does.
    if fix_to:
        question = (
            f"Someone spelled it out: **{_md(fix_to)}**. Fix the spelling, or keep it as heard?"
        )
    elif match is not None and spelled:
        question = (
            f"Sounds like **{_md(match.name)}**. Same name, spelled **{_md(entity.name)}**? "
            "Or a new name?"
        )
    elif match is not None:
        question = f"Sounds like **{_md(match.name)}**. The same, or new?"
    else:
        question = (
            "Is this a name in your game?"
            if kind
            else "Is this a name in your game? Pick what it is to add it, or press 🚫 Not a name."
        )
    heard = (
        "" if fix_to else _heard(entity)
    )  # the note is the spelling: shown once, in the question
    read = f"\nDMbot's guess: {_bold_kind(kind)}." if kind else ""
    where = f"\n{places}" if places else ""  # where it was said (#2329), under the count
    # The sentence around a name read from the DM's text, held in memory since the reading (#2561).
    # (links kept as plain text too: a masked link in the DM's text must not become clickable)
    shown = discord.utils.escape_markdown(context, ignore_links=False)
    around = f"\nAround it in your text: \u201c{shown}\u201d" if context else ""
    return f"📝 **{_md(entity.name)}**{heard}{where}{around}{others}{read}\n{question}{more}"


def _and(items: list[str]) -> str:
    """ "A", "A and B", "A, B and C"."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def its_label(name: str) -> str:
    """ "✅ It's Hrothgar", shortened to fit a phone (the full name is in the question)."""
    return logic.shorten(f"✅ It's {name}", REVIEW_LABEL_MAX)


async def _review_match(memory: MemoryStore, campaign: Campaign, entity: Entity) -> Match | None:
    """The known name this suggestion sounds like, worked out now (names may have
    changed, or been made secret, since the session): one small query for names that
    share its sound codes, confirmed and never secret; none if it can't be read."""
    try:
        rows = await memory.sound_alikes(campaign.guild_id, campaign.id, sound_keys(entity.name))
    except Exception:
        log.exception("Couldn't look up names that sound like a suggestion")
        return None
    return near_match_in(entity.name, (r for r in rows if r[0] != entity.id))


async def start_review(
    interaction: discord.Interaction, campaign_id: str, *, bulk: bool = True
) -> None:
    memory = _memory(interaction)
    if memory is None:
        await _tell(interaction, NOT_AVAILABLE)
        return
    campaign = await _campaign_for(interaction, campaign_id)
    if campaign is None:
        return
    await _review(interaction, memory, campaign, bulk=bulk)


async def _review(
    interaction: discord.Interaction, memory: MemoryStore, campaign: Campaign, *, bulk: bool = True
) -> None:
    waiting = await memory.entities(campaign.guild_id, campaign.id, statuses=[PROPOSED])
    if not waiting:
        await _tell(interaction, "✅ No new names to check. DMbot suggests some after a session.")
        from dmbot.ui.name_background import offer_ready  # (it imports this module)

        await offer_ready(interaction, campaign.id)  # a backstory's connections, if ready
        return
    waiting.sort(key=lambda e: (e.created_at, e.name))
    if bulk and await _offer_add_all(interaction, memory, campaign, waiting):
        return
    view = SuggestionReview(campaign.id, [e.id for e in waiting])
    text = await view.prepare(interaction, campaign, memory, waiting[0])
    await _send(interaction, text, view)


async def _offer_add_all(
    interaction: discord.Interaction,
    memory: MemoryStore,
    campaign: Campaign,
    waiting: Sequence[Entity],
) -> bool:
    """The lore review's "✅ Add all 4 places" for the name scan's suggestions that were given a
    kind (#2937), opened by the same persistent "Check new names" button, so it works after a
    restart. Only names with nothing to look at first: one that sounds like a known name, or that
    someone spelled out, is checked on its own card. True when it was offered (its "Check them one
    by one" opens the cards); False when there is nothing to add in bulk."""
    from dmbot.ui.name_background import BulkConfirm  # (it imports this module)

    by_kind: dict[str, list[str]] = {}
    for entity in waiting:
        kind = background_kind(entity)
        if (
            not kind
            or entity.source != "scan"
            or entity.description.startswith(SPELLED_OUT)
            or spelled_fix(entity)
            or await _review_match(memory, campaign, entity) is not None
        ):
            continue
        by_kind.setdefault(kind, []).append(entity.id)
    if not any(len(ids) > 1 for ids in by_kind.values()):  # one name is one press on its card
        return False
    count = sum(len(ids) for ids in by_kind.values())
    await _send(
        interaction,
        f"📝 **{_plural(len(waiting), 'new name')} to check.** DMbot guessed what {count} of them "
        "are. Nothing is added until you press a button.",
        BulkConfirm(campaign.id, by_kind),
    )
    return True


class ChangeWordsForm(discord.ui.Modal, title="What is the whole name?"):
    """The name card's ✏️ Change the words (#2570): the name filled in, to extend or fix."""

    # (a modal has no body text, and the placeholder hides once the name is filled in)
    name: discord.ui.TextInput[ChangeWordsForm] = discord.ui.TextInput(
        label="Whole name, as it should read",
        placeholder="Add the words around it, or fix the spelling.",
        max_length=NAME_MAX,
    )

    def __init__(self, review: SuggestionReview, entity_id: str, start: str, heard: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.review = review
        self.entity_id = entity_id
        self.heard = heard  # the longer form as heard, when one was picked
        self.name.default = start

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.review.apply_words(interaction, self.entity_id, self.heard, self.name.value)


class WordsPicker(_Menu):
    """Up to three likely longer names for the DM to pick from (#2570), or to type their own."""

    def __init__(self, review: SuggestionReview, entity: Entity, candidates: list[str]) -> None:
        super().__init__()
        self.review = review
        self.entity = entity
        self.candidates = candidates
        for i, words in enumerate(candidates):
            self.add_item(
                _Button(
                    partial(self._picked, words),
                    label=logic.shorten(f"{i + 1}. {words}", REVIEW_LABEL_MAX),
                    style=discord.ButtonStyle.primary,
                    row=i,
                )
            )
        self.add_item(
            _Button(self._own, label="✏️ Type it myself", style=discord.ButtonStyle.secondary, row=3)
        )
        self.add_item(
            _Button(self._back, label="↩ Back", style=discord.ButtonStyle.secondary, row=3)
        )

    async def _picked(self, words: str, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ChangeWordsForm(self.review, self.entity.id, words, words)
        )

    async def _own(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ChangeWordsForm(self.review, self.entity.id, self.entity.name, "")
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        current = await self.review._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        text = await self.review.prepare(interaction, campaign, memory, entity)
        await _replace(interaction, text, self.review)


class SuggestionReview(_Menu):
    """One suggested name at a time: Yes (then what it is), Same as…, Not a name, Later."""

    def __init__(self, campaign_id: str, entity_ids: list[str]) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.queue = entity_ids
        self.later = 0  # left for another time this round
        self.match: Match | None = None  # the known name the current one sounds like
        self.known_kind = ""  # the kind a name from the DM's background already has (#2167)
        self.name = ""  # the current suggestion's name, for the picker on its card (#2937)
        self.spelled = ""  # the spelling, when the current one was spelled out at the table (#2168)
        self.fix_to = ""  # how someone spelled this pending suggestion, when it differs (#2168)
        self.also: list[str] = []  # names heard with the current one
        self.places = name_places.Places()  # where the current one was said (#2329)
        self.spellings: list[str] = []
        self._buttons()

    def _added(self, entity: Entity) -> str:
        """ "**Oskar Vane** (also **Vane**)": every name confirmed with it."""
        also = f" (also {_and([f'**{_md(a)}**' for a in self.also])})" if self.also else ""
        return f"**{_md(entity.name)}**{also}"

    async def prepare(
        self,
        interaction: discord.Interaction,
        campaign: Campaign,
        memory: MemoryStore,
        entity: Entity,
    ) -> str:
        """The current suggestion's text, with its buttons set for it."""
        self.match = await _review_match(memory, campaign, entity)
        # A name read from the DM's background already has its kind; "Yes" confirms it as that.
        self.known_kind = background_kind(entity)
        self.name = entity.name
        self.spelled = entity.name if entity.description.startswith(SPELLED_OUT) else ""
        self.fix_to = spelled_fix(entity)
        own = name_key(entity.name)
        self.also = [
            a.text
            for a in await memory.aliases(campaign.guild_id, campaign.id, entity_id=entity.id)
            if a.key != own
        ]
        self.spellings = [entity.name, *self.also]
        self.places = await self._places(interaction, campaign, memory, entity)
        self._buttons()
        return suggestion_text(
            entity,
            len(self.queue),
            also=self.also,
            match=self.match,
            spelled=bool(self.spelled),
            fix_to=self.fix_to,
            kind=self.known_kind,
            places=self.places.text(),
            context=self._context(interaction, campaign, entity),
        )

    @staticmethod
    def _context(interaction: discord.Interaction, campaign: Campaign, entity: Entity) -> str:
        """The sentence around this name in the DM's text, while it is still held (#2561). Only for
        the campaign's DMs: the text can hold secrets, and a server manager who is not a DM may
        be at the table (`sees_secrets`)."""
        held = getattr(interaction.client, "lore_context", None)
        if held is None or not sees_secrets(campaign, interaction.user.id):
            return ""
        return str(held.get(campaign.guild_id, entity.id))

    async def _places(
        self,
        interaction: discord.Interaction,
        campaign: Campaign,
        memory: MemoryStore,
        entity: Entity,
    ) -> name_places.Places:
        """Where the current suggestion was said, from the pointers kept with it; none for a
        name that has none (read from a background, or suggested before places were kept)."""
        transcripts = getattr(interaction.client, "transcripts", None)
        if transcripts is None:
            return name_places.Places()
        try:
            refs = await memory.mentions(campaign.guild_id, campaign.id, entity.id)
        except Exception:
            log.exception("Couldn't read where a suggested name was said")
            return name_places.Places()
        site = getattr(getattr(interaction.client, "settings", None), "site_url", "") or ""
        return await name_places.first_places(
            interaction.guild,
            transcripts,
            campaign.guild_id,
            campaign.id,
            refs,
            self.spellings,
            await cast(Any, interaction.client).consent.consenting(
                campaign.guild_id
            ),  # who agrees now
            site if isinstance(site, str) else "",
        )

    async def _more_places(self, interaction: discord.Interaction) -> None:
        """Up to five more windows, in a private message of their own."""
        transcripts = getattr(interaction.client, "transcripts", None)
        if transcripts is None or not self.places.more:
            await _tell(interaction, name_places.GONE)
            return
        campaign = await _campaign_for(interaction, self.campaign_id)
        if campaign is None:
            return
        await _answer_first(interaction)
        text = await name_places.more_places(
            interaction.guild,
            transcripts,
            campaign.guild_id,
            self.places.more,
            self.spellings,
            await cast(Any, interaction.client).consent.consenting(campaign.guild_id),
        )
        await interaction.followup.send(text, ephemeral=True)

    def _buttons(self) -> None:
        self.clear_items()
        if self.fix_to:  # someone spelled this suggestion out: fix its spelling first (#2168)
            buttons = [
                (self._fix_pending, "✏️ Fix spelling", discord.ButtonStyle.success),
                (self._yes, "✅ Yes, as heard", discord.ButtonStyle.primary),
                (self._same, "🔗 Same as a known name…", discord.ButtonStyle.secondary),
            ]
        elif self.match is not None:  # sounds like a known name: offer that first (#394)
            buttons = [
                (self._another, its_label(self.match.name), discord.ButtonStyle.success),
                (self._yes, "➕ New name", discord.ButtonStyle.primary),
                (self._same, "🔗 Another known name…", discord.ButtonStyle.secondary),
            ]
            if self.spelled:  # someone spelled it out: offer to fix the known name's spelling
                buttons.insert(
                    1,
                    (
                        self._fix_spelling,
                        "✏️ Fix spelling",
                        discord.ButtonStyle.primary,
                    ),
                )
        elif self.known_kind:  # read with a kind: say it, and let the DM change it (#2167)
            buttons = [
                (
                    self._yes,
                    logic.shorten(f"✅ Yes, {a_kind(self.known_kind)}", REVIEW_LABEL_MAX),
                    discord.ButtonStyle.success,
                ),
                (self._pick_kind, "✏️ Something else", discord.ButtonStyle.primary),
                (self._same, "🔗 Same as a known name…", discord.ButtonStyle.secondary),
            ]
        else:
            # No kind was guessed: the picker is on the card itself, so one press still adds it
            # (#2937). "Same as a known name" waits beside the "not now" answers below.
            buttons = []
            self.kind = _Select(
                self._kind_picked,
                placeholder=logic.shorten(f"What is {self.name or 'it'}? Pick to add it", 150),
                options=[
                    discord.SelectOption(label=label, value=kind) for kind, label in KINDS.items()
                ],
                row=0,
            )
            self.add_item(self.kind)
            self.add_item(
                _Button(
                    self._same,
                    label="🔗 Same as a known name…",
                    style=discord.ButtonStyle.primary,
                    row=1,
                )
            )
        for handler, label, style in buttons:
            self.add_item(_Button(handler, label=label, style=style, row=0))
        # The two "not now" answers on their own row, so a phone never cuts the labels.
        self.add_item(
            _Button(self._no, label="🚫 Not a name", style=discord.ButtonStyle.secondary, row=1)
        )
        self.add_item(
            _Button(self._later, label="⏳ Later", style=discord.ButtonStyle.secondary, row=1)
        )
        # The name may be longer or spelled differently than the scan found it (#2570).
        self.add_item(
            _Button(
                self._change_words,
                label="✏️ Change the words",
                style=discord.ButtonStyle.secondary,
                row=1,
            )
        )
        if self.places.more:  # more windows behind a button (#2329)
            self.add_item(
                _Button(
                    self._more_places,
                    label="🔎 More places",
                    style=discord.ButtonStyle.secondary,
                    row=2,
                )
            )
        if self.places.link:  # the session's page on the website
            self.add_item(
                discord.ui.Button(label="📜 Open in transcript", url=self.places.link, row=2)
            )

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
                text = await self.prepare(interaction, campaign, memory, entity)
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
        from dmbot.ui.name_background import offer_ready  # (it imports this module)

        await offer_ready(interaction, self.campaign_id)  # a backstory's connections, if ready

    async def _saved(
        self,
        interaction: discord.Interaction,
        campaign: Campaign,
        memory: MemoryStore,
        note: str,
    ) -> None:
        decided = self.queue.pop(0)
        held = getattr(interaction.client, "lore_context", None)
        if held is not None:
            held.forget(campaign.guild_id, decided)  # decided: its sentence is not needed (#2561)
        await self._next(interaction, campaign, memory, note=note)

    async def _yes(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        if self.known_kind:  # it was read with its kind: one press (#2167)
            try:
                await memory.confirm_entity(
                    campaign.guild_id, campaign.id, entity.id, self.known_kind, source=DM
                )
            except MemoryRuleError:
                await _tell(interaction, GONE)
                return
            changed(interaction, campaign)
            await self._saved(interaction, campaign, memory, f"✅ Added {self._added(entity)}.")
            return
        await self._ask_kind(interaction, entity)

    async def _pick_kind(self, interaction: discord.Interaction) -> None:
        """ "✏️ Something else": the kind picker, for a name that was read with another kind."""
        current = await self._current(interaction)
        if current is not None:
            await self._ask_kind(interaction, current[2])

    async def _ask_kind(self, interaction: discord.Interaction, entity: Entity) -> None:
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
            self.add_item(self._type_id(campaign, entity))
            self.add_item(_Button(self._back, label="Back", style=discord.ButtonStyle.secondary))
            await _replace(interaction, f"**Who plays {_md(entity.name)}?**", self)
            return
        try:
            await memory.confirm_entity(campaign.guild_id, campaign.id, entity.id, kind, source=DM)
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)  # the next card sees it at once
        await self._saved(interaction, campaign, memory, f"✅ Added {self._added(entity)}.")

    def _type_id(self, campaign: Campaign, entity: Entity) -> _Button:
        """⌨️ Type a Discord ID, for a player who is not in the server yet (#3004). It is for this
        suggestion only: a confirmation that comes after the queue moved on links nothing."""

        async def allowed(interaction: discord.Interaction) -> bool:
            return await _campaign_for(interaction, self.campaign_id) is not None

        async def linked(interaction: discord.Interaction, user_id: int, name: str) -> None:
            await self._link_player(interaction, user_id, name, expected=entity.id)

        return player_id.type_id_button(campaign.guild_id, allowed, linked, self._back)

    async def _player_picked(
        self, interaction: discord.Interaction, player: discord.User | discord.Member
    ) -> None:
        if player.bot:
            await _tell(interaction, "Pick a person, not a bot.")
            return
        await self._link_player(interaction, player.id, player.display_name)

    async def _link_player(
        self, interaction: discord.Interaction, user_id: int, name: str, expected: str | None = None
    ) -> None:
        await _answer_first(interaction, in_place=True)  # (the save and the next card follow)
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        if expected is not None and entity.id != expected:
            await _tell(interaction, "That one's already done.")
            return
        try:
            await memory.confirm_entity(
                campaign.guild_id, campaign.id, entity.id, PC, source=DM, played_by=user_id
            )
        except MemoryRuleError:
            await _tell(interaction, GONE)
            return
        changed(interaction, campaign)
        note = f"✅ Added {self._added(entity)}, played by **{_md(name)}**."
        await self._saved(interaction, campaign, memory, note)

    async def _same(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        known = await memory.entities(campaign.guild_id, campaign.id, statuses=[CONFIRMED])
        if not known:
            await _tell(
                interaction,
                "DMbot doesn't know any names yet, so there's nothing to match. "
                "Add this one as a new name instead.",
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
                    description=KIND_SHORT.get(e.kind_key, e.kind_key),
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
        await self._same_as(interaction, self.same.values[0])

    async def _another(self, interaction: discord.Interaction) -> None:
        """One press: the suggestion is another name for the known name it sounds like."""
        if self.match is None:
            await _tell(interaction, GONE)
            return
        await self._same_as(interaction, self.match.entity_id)

    async def _fix_pending(self, interaction: discord.Interaction) -> None:
        """Someone spelled out a pending suggestion (#2168): it takes that spelling, and the card
        comes back with the new name for the DM to answer. The DM decided; nothing is added."""
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        spelling = spelled_fix(entity)
        if not spelling:
            await _tell(interaction, GONE)
            return
        try:
            await memory.rename_entity(
                campaign.guild_id, campaign.id, entity.id, spelling, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc}")
            return
        changed(interaction, campaign)
        await self._next(interaction, campaign, memory, note=f"✏️ Now spelled **{_md(spelling)}**.")

    async def _fix_spelling(self, interaction: discord.Interaction) -> None:
        """Someone spelled the name out at the table (#2168) and it sounds like a known name with
        another spelling: the known name takes this spelling as its main name and keeps the old
        spelling as another name, as ⭐ does. Merging keeps its guards."""
        if self.match is None:
            await _tell(interaction, GONE)
            return
        await self._same_as(interaction, self.match.entity_id, main=True)

    async def _same_as(
        self, interaction: discord.Interaction, keep_id: str, *, main: bool = False
    ) -> None:
        """The DM said the suggestion is the same as a known entry: its names (and any
        heard with it) become that entry's other names. Merging keeps its guards
        (secret names, player characters) and can be undone."""
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        gid, cid = campaign.guild_id, campaign.id
        keep = await memory.entity(gid, cid, keep_id)
        if keep is None or keep.status != CONFIRMED:
            await _tell(interaction, GONE)
            return
        names = [entity.name, *self.also]
        # Answer Discord at once (#351): a big merge can take longer than its 3 seconds.
        # Saying so in place also takes the menu away, so nothing is picked twice.
        await _replace(
            interaction, f"🔗 Joining **{_md(entity.name)}** into **{_md(keep.name)}**…", None
        )
        try:
            # One change: the merge and the names confirmed with it, so one undo.
            await memory.merge(
                gid,
                cid,
                keep.id,
                entity.id,
                source=DM,
                dm_said_same=True,
                confirm_keys=[name_key(n) for n in names],
            )
        except MemoryRuleError:
            await self._next(interaction, campaign, memory, note=NOT_JOINED)  # back in place
            return
        shown = _and([f"**{_md(n)}**" for n in names])
        verb = "are now names" if len(names) > 1 else "is now a name"
        note = f"✅ Got it: {shown} {verb} for **{_md(keep.name)}**."
        if main:  # the spelled-out one becomes the main name; the old spelling stays (#2168)
            note = await self._respelled(memory, campaign, keep, entity.name, note)
        changed(interaction, campaign)
        await self._saved(interaction, campaign, memory, note)

    async def _respelled(
        self, memory: MemoryStore, campaign: Campaign, keep: Entity, spelling: str, note: str
    ) -> str:
        """Make the merged-in spelling the main name, before the DM is answered. A failure leaves
        the merge as it is and says so."""
        try:
            for alias in await memory.aliases(campaign.guild_id, campaign.id, entity_id=keep.id):
                if alias.key == name_key(spelling) and alias.status == CONFIRMED:
                    await memory.set_main_name(
                        campaign.guild_id, campaign.id, keep.id, alias.id, source=DM
                    )
                    return (
                        f"✅ Got it: **{_md(keep.name)}** is now spelled **{_md(spelling)}**. "
                        f"The old spelling is still one of its names."
                    )
        except MemoryRuleError as exc:
            log.info("A spelled-out name could not be made the main name: %s", exc)
        except Exception:
            log.exception("Couldn't make a spelled-out name the main name")
        return f"{note} (Its spelling is unchanged: use ⭐ on its card to change it.)"

    def _quotes(self, entity: Entity) -> list[str]:
        """The words around this name that the card already shows, for the likely longer names:
        the transcript windows (their bold marks are dropped by `longer_names`)."""
        return list(self.places.windows)

    async def _change_words(self, interaction: discord.Interaction) -> None:
        """The name is longer or different (#2570): offer the likely longer names from the card's
        quotes, or go straight to the form when there are none."""
        current = await self._current(interaction)
        if current is None:
            return
        campaign, _memory_unused, entity = current
        if not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, "Only the DM can change a name.")
            return
        candidates = longer_names(self._quotes(entity), entity.name)
        if not candidates:
            await interaction.response.send_modal(ChangeWordsForm(self, entity.id, entity.name, ""))
            return
        await _replace(
            interaction,
            f"**What is the whole name?** DMbot heard **{_md(entity.name)}**. Tap the full name, "
            "or type it yourself. You can still change it before it is saved. Back changes "
            "nothing.\n" + "\n".join(f"{n}. {_md(c)}" for n, c in enumerate(candidates, 1)),
            WordsPicker(self, entity, candidates),
        )

    async def apply_words(
        self, interaction: discord.Interaction, entity_id: str, heard: str, words: str
    ) -> None:
        """The DM saved the whole name (#2570): the card goes on with it, as with a fixed
        spelling. What was heard becomes a way it is said, so the Cleaner fixes it in later
        transcripts, and the old single word stops being offered where it is part of the new name
        (it is not marked "not a name": said alone elsewhere it can still be offered)."""
        await _answer_first(interaction, in_place=True)
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        if entity.id != entity_id or not sees_secrets(campaign, interaction.user.id):
            await _tell(interaction, GONE)
            return
        new = " ".join(words.split())
        if not new:
            await _tell(interaction, "The name was empty. Press ✏️ Change the words and type it.")
            return
        if len(new) > NAME_MAX:
            await _tell(
                interaction,
                f"**{_md(logic.shorten(new, 60))}** is too long (the limit is {NAME_MAX} letters). "
                "Shorten it and try again.",
            )
            return
        if name_key(new) != name_key(entity.name):
            clash = await known_as(memory, campaign, new)
            if clash is not None:
                await _tell(interaction, already_known(new, clash))
                return
        # The old word is part of the new name: keeping it as an old spelling would turn every
        # "Harrow" said alone into "Harrow's Keep".
        part = widens(entity.name, new)
        try:  # one change, so one Undo: the new name and how it was heard
            await memory.rename_entity(
                campaign.guild_id,
                campaign.id,
                entity.id,
                new,
                source=DM,
                keep_old=not part,
                also_known_as=" ".join(heard.split()),
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change it. {exc} Nothing was changed.")
            return
        changed(interaction, campaign)
        await self._next(interaction, campaign, memory, note=f"✏️ Saved as **{_md(new)}**.")

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
        changed(interaction, campaign)
        names = _and([f"**{_md(n)}**" for n in [entity.name, *self.also]])
        verb = "aren't names" if self.also else "isn't a name"
        note = f"🚫 OK, {names} {verb}. DMbot won't suggest {'them' if self.also else 'it'} again."
        await self._saved(interaction, campaign, memory, note)

    async def _later(self, interaction: discord.Interaction) -> None:
        current = await self._current(interaction)
        if current is None:
            return
        campaign, memory, entity = current
        self.later += 1
        self.queue.pop(0)  # this round only; it's still waiting next time
        try:  # and remembered, so it no longer holds a recap back (#2174)
            await memory.defer_name(campaign.guild_id, campaign.id, entity.id)
        except Exception:
            log.exception("Couldn't remember a name left for later")
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


class RecapNamesButton(
    discord.ui.DynamicItem[discord.ui.Button[discord.ui.View]],
    template=r"dmbot:names:recap:(?P<guild>[0-9]{1,20}):(?P<campaign>[0-9a-f]{32})",
):
    """The same check, in the private message the DM gets when a recap used names DMbot didn't
    know (#2027). A private message has no server, so the server is in the button's ID. Only a DM
    of the campaign may use it; it works after a restart."""

    def __init__(self, guild_id: int, campaign_id: str) -> None:
        super().__init__(
            discord.ui.Button(
                label="📝 Check new names",
                style=discord.ButtonStyle.primary,
                custom_id=f"dmbot:names:recap:{guild_id}:{campaign_id}",
            )
        )
        self.guild_id, self.campaign_id = guild_id, campaign_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str]
    ) -> RecapNamesButton:
        return cls(int(match["guild"]), match["campaign"])

    async def callback(self, interaction: discord.Interaction) -> Any:
        memory = _memory(interaction)
        if memory is None:
            await _tell(interaction, NOT_AVAILABLE)
            return
        campaign = await _bot(interaction).campaigns.get(self.guild_id, self.campaign_id)
        if campaign is None or not logic.can_run(campaign, interaction.user.id, False):
            await _tell(interaction, logic.NO_CAMPAIGN_ACCESS)
            return
        if interaction.guild is None:
            _PRIVATE_SERVERS[campaign.id] = campaign.guild_id  # so the cards' buttons work here
        await _review(interaction, memory, campaign)


def recap_names_dm_view(guild_id: int, campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RecapNamesButton(guild_id, campaign_id))
    return view


def review_view(campaign_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ReviewButton(campaign_id))
    return view


def held_back_text(names: list[str]) -> str:
    """For the DM screen, when names came from what was said (#1364): the same check as the name
    scan's, but it says why some of what was said is not asked yet. Names only, no claims."""
    shown = ", ".join(f"**{_md(n)}**" for n in names[:5])
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    return (
        f"📝 **{_plural(len(names), 'new name')} to check** from this session: {shown}{more}.\n"
        "What was said about them is held back until you check them. Say which are in your "
        "game: the check opens privately for the DM."
    )


def recap_names_text(count: int) -> str:
    """For the DM screen when a recap used names DMbot didn't know yet (#2027): the names are
    listed on the DM's recap page, not here, so nothing is spoiled for players who peek."""
    return (
        f"📝 **{_plural(count, 'new name')} in the recap.** Said at your table, and DMbot "
        "doesn't know them yet. Tap **Check new names** to say which are in your game. "
        "Only you will see them."
    )


def recap_names_reminder(count: int) -> str:
    return (
        f"📝 **{_plural(count, 'new name')}** from the last recap "
        f"{'is' if count == 1 else 'are'} still unchecked. Tap **Check new names** when you "
        "have a moment."
    )


def private_after_session_text(names: list[str], where: str = "this session") -> str:
    """The same question, in the DM's private chat (#3210): nobody else reads it, so nothing here
    is held back. Names only, as on the DM screen."""
    shown = ", ".join(f"**{_md(n)}**" for n in names[:5])
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    return (
        f"📝 **{_plural(len(names), 'new name')} to check** from {where}: {shown}{more}.\n"
        "Say which are in your game, so DMbot spells them right."
    )


def after_session_text(names: list[str], where: str = "this session") -> str:
    """For the DM screen (players may see it under peek; the names come from the
    transcript anyone can read, so nothing is spoiled)."""
    shown = ", ".join(f"**{_md(n)}**" for n in names[:5])
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    return (
        f"📝 **{_plural(len(names), 'new name')} to check** from {where}: {shown}{more}.\n"
        "Say which are in your game, so DMbot spells them right. The check opens privately "
        "for the DM."
    )
