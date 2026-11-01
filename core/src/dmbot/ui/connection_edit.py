"""✏️ Edit a connection…: change the words of one connection, who sees it, or take it back (#2323).

The name card's Connect menu lists a name's connections in the card's own order and words (🤫 on
secret ones). Choosing one opens its page here: other words, hide or show it, and remove it
(asks once). Past Discord's 25 choices the last one sends the DM to the website's page for the
whole list, as on the names menus (#2294). Each change goes through `memory/link_edits.py`, the
calls the website's relationship web uses too, so Undo and the change log agree. Every press checks
the campaign, the DM and the connection again; only the campaign's DMs deal in secrets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import link_edits
from dmbot.memory.link_words import CONNECTION_WORDS, link_words, predicate_for
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, MemoryRuleError, Relation
from dmbot.memory.store import MemoryStore
from dmbot.memory.who_resolve import checked_line
from dmbot.mentions import NO_PINGS
from dmbot.ui import all_names, logic
from dmbot.ui.dmbot_commands import (
    VIEW_TIMEOUT_S,
    _answer_first,
    _Button,
    _failed,
    _Menu,
    _replace,
    _Select,
    _tell,
)
from dmbot.ui.name_card import (
    NOTHING_MORE,
    SECRET_DENIED_PRESS,
    Row,
    _names,
    _rows,
    sentence,
    show_card,
)
from dmbot.ui.names import _campaign_for, _md, _memory, changed, sees_secrets
from dmbot.ui.who_knows import WhoKnowsMenu, called, player_characters, who_text

WHO_BUTTON = "👁️ Who knows this?"
EDIT_BUTTON = "✏️ Edit a connection…"  # (the site's help names it: web/test/button-labels)
ALL_VALUE = "dmbot:all-links"  # no connection's id looks like this
ALL_LABEL = "🌐 All connections on the website…"
WEBSITE_BUTTON = "Open all connections"
WEBSITE_LINE = (
    "Every connection is on the website. Tap the button to open it. You may need to sign in."
)
GONE_LINK = "That connection isn't there any more. Open the name card again to see the list."
UNDO_NOTE = "Wrong? Press **Undo** within 10 minutes."
WORDS_MAX = 100  # the longest "other words": the same as the website's and the store's


def plain(row: Row) -> str:
    """The card's line for this connection, as plain text a menu can show."""
    return f"{row.words} {row.other}" + (" 🤫" if row.secret else "")


def choices(rows: list[Row]) -> list[discord.SelectOption]:
    """The menu's choices in the card's order, 25 at most: past that, 24 and the website."""
    options = [
        discord.SelectOption(label=logic.shorten(plain(row), logic.OPTION_LABEL_MAX), value=row.id)
        for row in rows
    ]
    if len(options) <= logic.SELECT_OPTIONS_MAX:
        return options
    return [
        *options[: logic.SELECT_OPTIONS_MAX - 1],
        discord.SelectOption(label=ALL_LABEL, value=ALL_VALUE),
    ]


def want_website(values: list[str]) -> bool:
    return bool(values) and values[0] == ALL_VALUE


async def website(interaction: discord.Interaction, campaign_id: str) -> None:
    """The private answer to the last choice: one link button, or a plain note without a site."""
    settings = getattr(interaction.client, "settings", None)
    site = str(getattr(settings, "site_url", "") or "")
    url = all_names.page_url(site, interaction.guild_id or 0, campaign_id)
    if not interaction.guild_id or not url:
        await _tell(interaction, all_names.NO_SITE)
        return
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label=WEBSITE_BUTTON, emoji="🔗", url=url))
    if interaction.response.is_done():
        await interaction.followup.send(
            WEBSITE_LINE, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )
    else:
        await interaction.response.send_message(
            WEBSITE_LINE, view=view, ephemeral=True, allowed_mentions=NO_PINGS
        )


@dataclass
class Found:
    campaign: Campaign
    memory: MemoryStore
    names: CampaignLookup
    relation: Relation
    secrets: bool
    pinned: bool = False  # the DM's 📌 100% truth (#2447); only read for a DM


async def _find(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, relation_id: str
) -> Found | None:
    """Everything one press needs, checked now: the campaign, the person's right, the connection
    still there, confirmed and one this person may see. Tells them if not."""
    campaign = await _campaign_for(interaction, campaign_id)
    memory = _memory(interaction)
    if campaign is None or memory is None:
        return None
    names = await _names(interaction, campaign)
    if names is None:
        return None
    secrets = sees_secrets(campaign, interaction.user.id)
    mine = await memory.relations(
        campaign.guild_id,
        campaign.id,
        entity_id=entity_id,
        confirmed_only=True,
        include_secret=secrets,
    )
    shown = {row.id: row.relation for row in _rows(names, entity_id, mine, secrets)}
    relation = shown.get(relation_id)
    if relation is None or relation.status != CONFIRMED:
        await _tell(interaction, GONE_LINK)
        return None
    pinned = secrets and relation.id in await memory.pinned_connections(
        campaign.guild_id, campaign.id
    )
    return Found(campaign, memory, names, relation, secrets, pinned)


def _said(found: Found) -> str:
    return _md(sentence(found.names, found.relation) or "that connection")


async def show_one(
    interaction: discord.Interaction, campaign_id: str, entity_id: str, relation_id: str
) -> None:
    """The page for one connection, in place of the menu that listed it."""
    await _answer_first(interaction, in_place=True)
    found = await _find(interaction, campaign_id, entity_id, relation_id)
    if found is None:
        return
    view = OneConnection(campaign_id, entity_id, found)
    await _replace(
        interaction,
        f"**{_said(found)}**"
        + (" 🤫" if found.relation.secret else " 🙈" if found.relation.unrevealed else "")
        + (" 📌" if found.pinned else "")
        + "\n"
        + (checked_line(found.relation.decided or "") if found.secrets else "")  # DMs only
        + (
            "Change the words, hide it, or remove it."
            if found.secrets
            else "Change the words or remove it."
        ),
        view,
    )


class OneConnection(_Menu):
    def __init__(self, campaign_id: str, entity_id: str, found: Found) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.relation_id = found.relation.id
        # As shown now: a press flips THIS, never "the opposite". A connection the story is holding
        # back (a backstory one, #2404) is hidden from players like a secret one, and the same
        # switch shows it to them.
        self.secret = found.relation.secret or found.relation.unrevealed
        r = found.relation
        subject = found.names.entities.get(r.subject_id)
        other = found.names.entities.get(r.object_id)
        if subject is not None and other is not None:
            self.words = _Select(
                self._words_picked,
                placeholder="Change the words…",
                options=[
                    discord.SelectOption(
                        label=logic.shorten(words, logic.OPTION_LABEL_MAX),
                        value=predicate,
                    )
                    for predicate, words in CONNECTION_WORDS.items()
                    if predicate != r.predicate
                ],
            )
            self.add_item(self.words)
        grey = discord.ButtonStyle.secondary
        self.add_item(_Button(self._other_words, label="Other words…", style=grey, row=1))
        if found.secrets:  # only the campaign's DMs deal in secrets
            self.add_item(_Button(self._who, label=WHO_BUTTON, style=grey, row=1))
        if found.secrets:  # the DM's word that it is true in the story (#2447)
            self.pinned = found.pinned
            self.add_item(
                _Button(
                    self._pin,
                    label="📌 Unpin" if self.pinned else "📌 Pin as 100% truth",
                    style=grey,
                    row=2,
                )
            )
        self.add_item(_Button(self._remove, label="🗑 Remove", style=grey, row=1))
        self.add_item(_Button(self._back, label="Back", style=grey, row=2))

    async def _changed(
        self, interaction: discord.Interaction, found: Found, note: str, *, undo: int | None = None
    ) -> None:
        changed(interaction, found.campaign)
        self.stop()
        await show_card(
            interaction,
            found.campaign.id,
            self.entity_id,
            replace=True,
            note=note,
            undo_connection=undo,
        )

    async def _words_picked(self, interaction: discord.Interaction) -> None:
        predicate = self.words.values[0]
        if predicate not in CONNECTION_WORDS:
            await _tell(interaction, NOTHING_MORE)
            return
        await self._new_words(interaction, predicate, "")

    async def _new_words(
        self, interaction: discord.Interaction, predicate: str, detail: str
    ) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        c = found.campaign
        try:
            moved = await link_edits.change_words(
                found.memory, c.guild_id, c.id, found.relation, predicate, detail,
                first=self.entity_id,
            )  # fmt: skip
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change that. {exc}")
            return
        if moved is None:
            await _tell(interaction, "It already says that. Press Back to see the card.")
            return
        changed(interaction, c)
        self.stop()
        reads = _md(self._now_reads(found, moved.value, self.entity_id))
        await show_card(
            interaction, c.id, self.entity_id, replace=True,
            note=f"Changed to: {reads}. {UNDO_NOTE}",
            undo_connection=moved.batch,
        )  # fmt: skip

    @staticmethod
    def _now_reads(found: Found, stored: Relation, card_id: str) -> str:
        # From the row as stored, not the words typed: a family role may have put the two names
        # the other way round (#2457), and the note must say what the card will now say. A
        # connection with no direction ("is an enemy of") is said from the card the DM is on,
        # whichever way round the row happens to be stored, as the card itself reads it.
        first, second = stored.subject_id, stored.object_id
        if not stored.directional and second == card_id:
            first, second = second, first
        subject = found.names.entities.get(first)
        other = found.names.entities.get(second)
        words = link_words(stored.predicate, stored.detail, directional=stored.directional)
        return f"{subject.name if subject else '?'} {words} {other.name if other else '?'}"

    async def _other_words(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            OtherWords(self.campaign_id, self.entity_id, self.relation_id, self)
        )

    async def _who(self, interaction: discord.Interaction) -> None:
        """Open the three-way answer for this connection (#2935): the old 🤫 Hide and 👁️ Show,
        and the middle one between them, some player characters."""
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # checked on every press
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        pcs = await player_characters(found.memory, found.campaign)
        knowing = await found.memory.connection_knowers(
            found.campaign.guild_id, found.campaign.id, [found.relation.id]
        )
        who = link_edits.who_knows_now(found.relation, knowing.get(found.relation.id, []))
        names = dict(pcs)
        view = WhoKnows(
            self.campaign_id, self.entity_id, found.relation.id, pcs,
            chosen=[c for c in knowing.get(found.relation.id, []) if c in names],
            now=who,
        )  # fmt: skip
        await _replace(
            interaction,
            who_text(
                _said(found),
                who,
                knowing.get(found.relation.id, []),
                names,
                has_characters=bool(names),
            ),
            view,
        )

    async def _pin(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # checked on every press
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        target = not self.pinned  # from what the page showed: a double press never flips back
        if found.pinned == target:
            await _tell(interaction, "It's already like that. Press Back to see the card.")
            return
        c = found.campaign
        try:
            written = await found.memory.pin(c.guild_id, c.id, found.relation.id, target, source=DM)
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change that. {exc}")
            return
        note = (
            f"📌 Pinned: {_said(found)}. DMbot will tell you if a session goes against it. "
            "📌 Unpin takes it back."
            if target
            else f"📌 Unpinned: {_said(found)}."
        )
        await self._changed(interaction, found, note, undo=written.batch)

    async def _remove(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        self.stop()
        await _replace(
            interaction,
            f"Remove **{_said(found)}**?",
            ConfirmRemove(self.campaign_id, self.entity_id, self.relation_id),
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class ConfirmRemove(_Menu):
    """The one question before a connection goes."""

    def __init__(self, campaign_id: str, entity_id: str, relation_id: str) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.relation_id = relation_id
        self.add_item(_Button(self._yes, label="Yes, remove it", style=discord.ButtonStyle.danger))
        self.add_item(_Button(self._keep, label="Keep it", style=discord.ButtonStyle.secondary))

    async def _yes(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        c = found.campaign
        try:
            written = await link_edits.remove(found.memory, c.guild_id, c.id, found.relation)
        except MemoryRuleError:
            await _tell(interaction, GONE_LINK)
            return
        changed(interaction, c)
        self.stop()
        await show_card(
            interaction, c.id, self.entity_id, replace=True,
            note=f"Removed: {_said(found)}. {UNDO_NOTE}", undo_connection=written.batch,
        )  # fmt: skip

    async def _keep(self, interaction: discord.Interaction) -> None:
        self.stop()
        await show_card(interaction, self.campaign_id, self.entity_id, replace=True)


class OtherWords(discord.ui.Modal, title="Other words"):
    words: discord.ui.TextInput[OtherWords] = discord.ui.TextInput(
        label="How are they connected?",
        placeholder="For example: sworn on the ashes of",
        max_length=WORDS_MAX,
    )

    def __init__(
        self, campaign_id: str, entity_id: str, relation_id: str, page: OneConnection
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.campaign_id = campaign_id
        self.entity_id = entity_id
        self.relation_id = relation_id
        self.page = page

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = " ".join(str(self.words.value).split())
        if not text or not text.isprintable():
            await _tell(interaction, "Type the words that join the two names, like “sworn to”.")
            return
        predicate, detail = predicate_for(text)
        await self.page._new_words(interaction, predicate, detail)


class WhoKnows(WhoKnowsMenu):
    """The connection's page (#2935). Each press checks the campaign, the DM and the connection
    again; the three keep the meaning of the old Hide and Show buttons."""

    def __init__(
        self,
        campaign_id: str,
        entity_id: str,
        relation_id: str,
        pcs: Sequence[tuple[str, str]],
        *,
        chosen: Sequence[str] = (),
        now: str = "",
    ) -> None:
        super().__init__(pcs, chosen=chosen, now=now)
        self.campaign_id, self.entity_id, self.relation_id = campaign_id, entity_id, relation_id

    async def _back(self, interaction: discord.Interaction) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        self.stop()
        await show_one(interaction, self.campaign_id, self.entity_id, self.relation_id)

    async def _set(self, interaction: discord.Interaction, who: str) -> None:
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # checked on every press
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        c = found.campaign
        # Read now, not from the page: a double press, a stale page or the answer it is already
        # at writes nothing (and never turns a hidden connection back into a shown one).
        live = (await found.memory.connection_knowers(c.guild_id, c.id, [found.relation.id])).get(
            found.relation.id, []
        )
        if who == link_edits.who_knows_now(found.relation, live) and (
            who != link_edits.SOME_CHARACTERS or sorted(self.picked) == sorted(live)
        ):
            await _tell(
                interaction, "Already like that. Nothing changed. Press Back to see the card."
            )
            return
        try:
            written = await link_edits.set_who_knows(
                found.memory,
                c.guild_id,
                c.id,
                found.relation,
                who,
                self.picked,
                players_only=True,  # (this menu lists player characters only: NPC knowers stay)
            )
        except MemoryRuleError as exc:
            if str(exc) == link_edits.PICK_ONE:
                await _tell(
                    interaction,
                    "Nothing changed. Choose at least one character in the list, "
                    "then press 👥 Only some characters.",
                )
            else:
                await _tell(interaction, f"Couldn't change that. {exc}")
            return
        said = _said(found)
        undo: int | None = None
        if who == link_edits.EVERYONE:
            note = f"👁️ Players can see it now: {said}. {UNDO_NOTE}"
            undo = written.batch  # (only showing it can be undone; who knew it is set again)
        elif who == link_edits.SOME_CHARACTERS:
            mine = [self.names[x] for x in self.picked if x in self.names]
            verb = "knows" if len(mine) == 1 else "know"
            note = (
                f"👥 Only {called(mine)} {verb} it now (and the DM): {said}. "
                "Not right? Change it again."
            )
        else:
            note = (
                f"🤫 Hidden from players. Only the DM can see it now: {said}. "
                "Not right? Change it again."
            )
        changed(interaction, c)
        self.stop()
        await show_card(
            interaction, c.id, self.entity_id, replace=True, note=note,
            undo_connection=undo,
        )  # fmt: skip
