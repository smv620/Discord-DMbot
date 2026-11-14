"""✏️ Edit a connection…: change the words of one connection, who sees it, or take it back (#2323).

The name card's Connect menu lists a name's connections in the card's own order and words (🤫 on
secret ones). Choosing one opens its page here: other words, hide or show it, and remove it
(asks once). Past Discord's 25 choices the last one sends the DM to the website's page for the
whole list, as on the names menus (#2294). Each change goes through `memory/link_edits.py`, the
calls the website's relationship web uses too, so Undo and the change log agree. Every press checks
the campaign, the DM and the connection again; only the campaign's DMs deal in secrets.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import discord

from dmbot.campaigns import Campaign
from dmbot.memory import kin, link_edits, route_length, route_words
from dmbot.memory.link_words import CONNECTION_WORDS, link_words, predicate_for, reads_both_ways
from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, DM, KNOWS_HOWS, MemoryRuleError, Relation
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
    NOT_IN_MENU,
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

log = logging.getLogger(__name__)

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
LENGTH_BUTTON = "🛣️ Length"  # a route's stated length (#4255)
KEEP_LENGTH_BUTTON = "✅ Length is right"
NOTHING_CHANGED = "Nothing changed."
WORDS_MAX = 100  # the longest "other words": the same as the website's and the store's
# Whether two people have met (#3881): the DM's word, on a `knows` only.
HOW_PLACEHOLDER = "Have they met?"
# (a menu's value can't be empty, so "not said" travels as NOT_SAID)
NOT_SAID = "not-said"
# (each says it all without the question: a menu with a choice made never shows its placeholder)
HOW_CHOICES = (
    ("met", "Yes, they've met"),
    ("aware", "No, only heard of them"),
    (NOT_SAID, "Not said if they've met"),
)


def meets(found: Found) -> bool:
    """Whether this connection can say if they've met: a `knows` between two people (#3881)."""
    r = found.relation
    ends = [found.names.entities.get(r.subject_id), found.names.entities.get(r.object_id)]
    return r.predicate == "knows" and all(e is not None and e.type == "character" for e in ends)


def plain(row: Row) -> str:
    """The card's line for this connection, as plain text a menu can show."""
    return (
        f"{row.words} {row.other}"
        + (f" {row.after}" if row.after else "")
        + (" 🤫" if row.secret else "")
    )


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


# Which way a connection reads, the DM's choice (#4154): the same three as the website's toggle.
DIRECTION_PLACEHOLDER = "Which way does it read?"
BOTH = "both"  # (the other two values are the id of the name it reads from)
BOTH_LABEL = "Both ways ↔"


def direction_choices(
    r: Relation, first: str, second: str, said: Callable[[Relation], str]
) -> list[discord.SelectOption]:
    """ "Cerric → Wit", "Wit → Cerric", "Both ways ↔", the one it reads now chosen. A chosen menu
    never shows its placeholder, so each choice's second line is the sentence it would give
    (`said`: "Cerric is a rival of Wit"). Names are cut to a menu's name length first, so both ends
    and the arrow always show. A family role true of one name only ("niece") keeps its word and
    has no both-ways choice once it has a direction: the arrow says who holds the role."""
    a, b = logic.name_label(first), logic.name_label(second)
    role = kin.role_of(r.detail) if r.predicate == "kin_of" else None
    one_way_role = role is not None and kin.directional(role)

    def one_way(x: Relation) -> Relation:
        # (a role takes the arrow as its direction; any other kind is marked one way)
        return (
            dataclasses.replace(x, directional=True)
            if one_way_role
            else dataclasses.replace(x, both_ways=False)
        )

    turned = one_way(dataclasses.replace(r, subject_id=r.object_id, object_id=r.subject_id))
    both = reads_both_ways(r.predicate, r.detail, r.directional, r.both_ways, r.route_state)
    choices = [
        (f"{a} → {b}", r.subject_id, not both, said(one_way(r))),
        (f"{b} → {a}", r.object_id, False, said(turned)),
    ]
    if not (one_way_role and r.directional):
        choices.append((BOTH_LABEL, BOTH, both, said(dataclasses.replace(r, both_ways=True))))
    return [
        discord.SelectOption(
            label=logic.shorten(label, logic.OPTION_LABEL_MAX),
            value=value,
            default=now,
            description=logic.shorten(line, logic.DESCRIPTION_MAX) if line else None,
        )
        for label, value, now, line in choices
    ]


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
    # (the same rows the menu offered: an ended holding too, for the DM, #4115)
    shown = {
        row.id: row.relation for row in _rows(names, entity_id, mine, secrets, history=secrets)
    }
    relation = shown.get(relation_id)
    if relation is None or relation.status != CONFIRMED:
        await _tell(interaction, GONE_LINK)
        return None
    pinned = secrets and relation.id in await memory.pinned_connections(
        campaign.guild_id, campaign.id
    )
    return Found(campaign, memory, names, relation, secrets, pinned)


def _said(found: Found, *, feeling: bool = True) -> str:
    """The connection as one sentence. `feeling=False` is how a player reads it (#3610)."""
    return _md(sentence(found.names, found.relation, feeling=feeling) or "that connection")


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
        + await why_line(found)  # DMs only: the DM's own text
        + (
            (
                "Change the words, say if they've met, hide it, or remove it."
                if meets(found)
                else "Change the words, hide it, or remove it."
            )
            if found.secrets
            else "Change the words or remove it."
        ),
        view,
    )


# The DM's "Why?" (#3606), in the words Discord uses for the upload ("📜 Upload campaign lore") and
# the website's line panel uses for the same line.
WHY_LINE = 'Why: your lore "{label}" says \u201c{quote}\u201d'
WHY_NO_QUOTE = 'Read from your lore "{label}". DMbot didn\'t keep the words.'
# A connection DMbot inferred from the lore and the DM kept (#2959 item 5): never the DM's own word.
WHY_INFERRED = 'Why: DMbot\'s guess from your lore "{label}": {reason}'


async def why_line(found: Found) -> str:
    """Where a kept lore connection came from and the reader's short quote of the DM's own text,
    for the DM alone (#3606), as the website's line panel shows it. Empty for anyone else (a second
    lock: the page checks too), when nothing is recorded (a connection from a session reads its
    lines through "Where was it said?"), or when it can't be read: it never stops the page."""
    if not found.secrets:
        return ""
    c = found.campaign
    try:
        got = await found.memory.link_source(c.guild_id, c.id, found.relation.id)
    except MemoryRuleError:
        return ""  # (the campaign went since the page was found: nothing to say)
    except Exception:
        log.exception("Couldn't read where a connection came from")
        return ""
    if got is None:
        return ""
    label, quote, inferred = _md(got[0]), _md(got[1]), _md(got[2])
    if inferred:  # (DMbot's inference, never the DM's own words: #2959 item 5)
        return WHY_INFERRED.format(label=label, reason=inferred) + "\n"
    return (
        WHY_LINE.format(label=label, quote=quote) if quote else WHY_NO_QUOTE.format(label=label)
    ) + "\n"


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
                    if predicate != r.predicate and predicate not in NOT_IN_MENU
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
        if r.predicate == route_words.ROUTE_TO and not r.route_state and found.secrets:
            # a route's stated length, the DM's (#4255; the ux-critic's words): 🛣️ (📏 is Size)
            self.length_words = r.length_words or ""
            self.add_item(_Button(self._length, label=LENGTH_BUTTON, style=grey, row=3))
            if r.length_words is not None and r.length_from == "read":
                self.add_item(
                    _Button(self._keep_length, label=KEEP_LENGTH_BUTTON, style=grey, row=3)
                )
        if meets(found) and found.secrets:  # whether they've met: the DM's word (#3881)
            self.how = _Select(
                self._how_picked,
                placeholder=HOW_PLACEHOLDER,
                options=[
                    discord.SelectOption(
                        label=label,
                        value=how,
                        default=(how == NOT_SAID and not r.knows_how) or how == r.knows_how,
                    )
                    for how, label in HOW_CHOICES
                ],
                row=3,
            )
            self.add_item(self.how)
        if found.secrets and subject is not None and other is not None:  # (the DM's word, #4154)
            self.direction = _Select(
                self._direction_picked,
                placeholder=DIRECTION_PLACEHOLDER,
                options=direction_choices(
                    r,
                    subject.name,
                    other.name,
                    lambda x: sentence(found.names, x, feeling=True) or "",
                ),
                row=4,
            )
            self.add_item(self.direction)

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
        if reads_both_ways(
            stored.predicate, stored.detail, stored.directional, stored.both_ways,
            stored.route_state,
        ):  # fmt: skip
            # said of the two together, from the card's name: "Cerric and Wit are rivals" (#4154)
            if second == card_id:
                stored = dataclasses.replace(stored, subject_id=second, object_id=first)
            return sentence(found.names, stored, feeling=True) or "?"
        subject = found.names.entities.get(first)
        other = found.names.entities.get(second)
        words = link_words(
            stored.predicate,
            stored.detail,
            stored.directional,
            subject.type if subject else "",
            feeling=True,
            knows_how=stored.knows_how,
            route_state=stored.route_state,
        )
        return f"{subject.name if subject else '?'} {words} {other.name if other else '?'}"

    async def _how_picked(self, interaction: discord.Interaction) -> None:
        how = "" if self.how.values[0] == NOT_SAID else self.how.values[0]
        if how not in KNOWS_HOWS:
            await _tell(interaction, NOTHING_MORE)
            return
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # (only the campaign's DMs say this; checked again on every press)
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        c = found.campaign
        try:
            written = await found.memory.set_knows_how(
                c.guild_id, c.id, self.relation_id, how, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change that. {exc}")
            return
        if written.batch is None:
            await _tell(interaction, "It already says that. Press Back to see the card.")
            return
        reads = _md(self._now_reads(found, written.value, self.entity_id))
        await self._changed(
            interaction, found, f"Changed to: {reads}. {UNDO_NOTE}", undo=written.batch
        )

    async def _direction_picked(self, interaction: discord.Interaction) -> None:
        picked = self.direction.values[0]
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # (only the campaign's DMs say this; checked again on every press)
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        r = found.relation
        if picked != BOTH and picked not in (r.subject_id, r.object_id):
            await _tell(interaction, NOTHING_MORE)
            return
        c = found.campaign
        try:
            written = await found.memory.set_direction(
                c.guild_id, c.id, r.id, None if picked == BOTH else picked, source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change that. {exc}")
            return
        if written.batch is None:
            await _tell(interaction, "It already reads that way. Press Back to see the card.")
            return
        reads = _md(self._now_reads(found, written.value, self.entity_id))
        await self._changed(
            interaction, found, f"Changed to: {reads}. {UNDO_NOTE}", undo=written.batch
        )

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

    async def _length(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            RouteLengthForm(self.length_words, lambda i, text: self._set_length(i, text))
        )

    async def _set_length(self, interaction: discord.Interaction, text: str) -> None:
        """The DM's 🛣️ Length (#4255): their words as given, the amount read from them; an empty
        box takes it off. Saving DMbot's read as it stands makes it the DM's too."""
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:  # checked on every press
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        c = found.campaign
        try:
            written = await found.memory.set_route_length(
                c.guild_id, c.id, found.relation.id, text, on="connection", source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, f"Couldn't change that. {exc}")
            return
        if written.batch is None:
            await _tell(interaction, NOTHING_CHANGED)
            return
        await self._changed(interaction, found, length_note(written.value), undo=written.batch)

    async def _keep_length(self, interaction: discord.Interaction) -> None:
        """✅ Length is right (#4255): DMbot's read of the length is the DM's from now on."""
        await _answer_first(interaction, in_place=True)
        found = await _find(interaction, self.campaign_id, self.entity_id, self.relation_id)
        if found is None:
            return
        if not found.secrets:
            await _tell(interaction, SECRET_DENIED_PRESS)
            return
        c = found.campaign
        try:
            written = await found.memory.keep_route_length(
                c.guild_id, c.id, found.relation.id, on="connection", source=DM
            )
        except MemoryRuleError as exc:
            await _tell(interaction, str(exc))  # (the length changed meanwhile: plain words)
            return
        await self._changed(interaction, found, length_note(written.value), undo=written.batch)

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


class RouteLengthForm(discord.ui.Modal, title="How far is it?"):
    """A route's stated length in the DM's words (#4255): filled with the length it has, so the DM
    edits it; an empty box takes it off. Shared by a route connection and a route place."""

    words: discord.ui.TextInput[RouteLengthForm] = discord.ui.TextInput(
        label="In your words (leave empty to take it off)",
        placeholder="For example: two days by cart",
        max_length=route_length.WORDS_MAX,
        required=False,
    )

    def __init__(
        self,
        current: str,
        done: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_S)
        self.words.default = current or None
        self._done = done

    async def on_error(  # type: ignore[override]  # a form's has no item (discord.py)
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await _failed(interaction, error)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = " ".join(str(self.words.value or "").split())
        if text and not text.isprintable():
            await _tell(interaction, "Type how far it is, like “two days by cart”.")
            return
        await self._done(interaction, text)


def length_note(length: route_length.Length | None) -> str:
    """What the DM is told after a length change (#4255; the ux-critic's words)."""
    if length is None:
        return f"🛣️ Length taken off. {UNDO_NOTE}"
    return f"🛣️ Length kept: **{_md(length.words)}**. {UNDO_NOTE}"


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
            seen = _said(found, feeling=False)  # (what players read: no feeling word)
            note = f"👁️ Players can see it now: {seen}. {UNDO_NOTE}"
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
