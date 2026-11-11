"""📜 "Is Norro another name of Cerric?" (#3034): the other names an AI read of the DM's lore
suggested for characters the DM already confirmed. A suggested name is not used anywhere until the
DM says yes here (matching, fixes and connection ends all wait), and a no is remembered, so the
same text never asks again. The sentence of the text around the name is shown while DMbot still
holds it in memory (never stored); a secret name says so, and stays secret when confirmed."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import discord

from dmbot.memory import lore_context
from dmbot.memory import spellings as place_spellings
from dmbot.memory.models import (
    BACKGROUND_SOURCE,
    CONFIRMED,
    DM,
    REJECTED,
    Alias,
    MemoryRuleError,
)
from dmbot.mentions import NO_PINGS
from dmbot.ui import logic
from dmbot.ui.dmbot_commands import _bot, _Button, _Menu, _tell
from dmbot.ui.name_background import (
    ONLY_DMS,
    QUOTE_SHOWN,
    campaign_for,
    markdown_safe,
    memory_of,
)

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

PERSON_YES, PERSON_NO = "✅ Yes, the same person", "❌ No, someone else"
PLACE_YES, PLACE_NO = "✅ Yes, the same place", "❌ No, two places"
# A place known by an epithet (not a spelling of it): the place words, but No does not make a
# second place (#4097).
EPITHET_PLACE_YES, EPITHET_PLACE_NO = "✅ Yes, the same place", "❌ No, somewhere else"
# Any other kind (a group, a thing): neither a person nor a place (#4097).
OTHER_YES, OTHER_NO = "✅ Yes, the same one", "❌ No, a different one"
# An item (an object the party has): "thing" words (#4097).
ITEM_YES, ITEM_NO = "✅ Yes, the same thing", "❌ No, a different thing"
# The kinds a person is asked about, as `Entity.kind_key` names them (a character with no role too).
PERSON_KINDS = ("player_character", "npc", "character")


class NameQuestions(_Menu):
    """One suggested other name at a time: ✅ Yes, it is another name / ❌ No / ⏭️ Not now."""

    def __init__(self, campaign_id: str, held: lore_context.Held | None = None) -> None:
        super().__init__()
        self.campaign_id = campaign_id
        self.held = held  # the sentences of the text, in memory only (#2561)
        self.guild_id = 0
        self.current: tuple[Alias, str] | None = None
        self.yes = 0
        self.no = 0
        self.left = 0
        self.total = 0
        self.busy = asyncio.Lock()  # a second press waits for the first, so none is counted twice
        # The same place written another way (#3037) is asked in its own words: for it the
        # buttons and the line say "place", not "person".
        self.spelling = False
        self.separate = 0  # spellings answered "No, two places": each became a place of its own
        grey = discord.ButtonStyle.secondary
        self.yes_button = _Button(self._yes, label=PERSON_YES, style=discord.ButtonStyle.success)
        self.no_button = _Button(self._no, label=PERSON_NO, style=grey)
        self.add_item(self.yes_button)
        self.add_item(self.no_button)
        self.add_item(_Button(self._not_now, label="⏭️ Not now", style=grey))

    async def _waiting(self, interaction: discord.Interaction) -> list[tuple[Alias, str]]:
        memory = memory_of(interaction)
        campaign = await campaign_for(interaction, self.campaign_id)
        if memory is None or campaign is None:
            return []
        return await memory.suggested_names(campaign.guild_id, campaign.id)

    def _set(self, waiting: list[tuple[Alias, str]]) -> None:
        self.left = len(waiting)
        self.total = max(self.total, self.yes + self.no + self.left)
        self.current = waiting[0] if waiting else None
        self.spelling = False

    async def _mark_spelling(self, interaction: discord.Interaction | None) -> None:
        """Is the current question one place written two ways? Then it is asked in the words for a
        place. Read for the one card on show; anything unclear keeps the person words."""
        memory = memory_of(interaction) if interaction is not None else None
        if self.current is None or memory is None:
            # (no card to ask about: the person words, never a stale card's)
            self.yes_button.label, self.no_button.label = PERSON_YES, PERSON_NO
            return
        alias, entry = self.current
        try:
            entity = await memory.resolve(self.guild_id, self.campaign_id, alias.entity_id)
        except Exception:  # a failed read keeps the person words, as an unknown kind does
            log.exception("The kind of a suggested name could not be read")
            entity = None
        self.spelling = (
            entity is not None
            and entity.type == place_spellings.PLACE
            and place_spellings.place_key(alias.text) == place_spellings.place_key(entry)
        )
        # The words follow the kind of the entry, so a card after a place card is set again here.
        if self.spelling:
            yes, no = PLACE_YES, PLACE_NO
        elif entity is not None and entity.type == place_spellings.PLACE:
            yes, no = EPITHET_PLACE_YES, EPITHET_PLACE_NO
        elif entity is None or entity.kind_key in PERSON_KINDS:
            yes, no = PERSON_YES, PERSON_NO
        elif entity.kind_key == "item":
            yes, no = ITEM_YES, ITEM_NO
        else:
            yes, no = OTHER_YES, OTHER_NO
        self.yes_button.label, self.no_button.label = yes, no

    async def load(self, interaction: discord.Interaction) -> bool:
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:
            return False
        self.guild_id = campaign.guild_id
        self._set(await self._waiting(interaction))
        await self._mark_spelling(interaction)
        return self.current is not None

    def text(self, interaction: discord.Interaction | None = None) -> str:
        if self.current is None:
            turned = self.no - self.separate
            made = f" {self.separate} kept as places of their own." if self.separate else ""
            return f"🔎 Done. {self.yes} added as other names, {turned} turned down.{made}"
        alias, entry = self.current
        place = f" ({self.yes + self.no + 1} of {self.total})" if self.total > 1 else ""
        said = ""
        sentence = self.held.get(self.guild_id, alias.id) if self.held is not None else ""
        if sentence:
            said = f"From your text: “{markdown_safe(logic.shorten(sentence, QUOTE_SHOWN))}”\n"
        hidden = " It is a secret name: players will not see it." if alias.secret else ""
        if self.spelling:
            return (
                f"🔎 **{markdown_safe(alias.text)}** looks like another way of writing "
                f"**{markdown_safe(entry)}**.{place}\n"
                f"{said}Nothing changes until you choose.{hidden}"
            )
        return (
            f"🔎 **Is {markdown_safe(alias.text)} another name of "
            f"{markdown_safe(entry)}?**{place}\n"
            f"{said}Nothing uses the name until you say yes.{hidden}"
        )

    async def _show(
        self, interaction: discord.Interaction, waiting: list[tuple[Alias, str]]
    ) -> None:
        self._set(waiting)
        await self._mark_spelling(interaction)
        if self.current is None:
            self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=self.text(),
                view=self if self.current else None,
                allowed_mentions=NO_PINGS,
            )

    async def _answer(self, yes: bool, interaction: discord.Interaction) -> None:
        shown = self.current  # the card this press was made on, taken before any wait
        await interaction.response.defer()
        async with self.busy:
            campaign = await campaign_for(interaction, self.campaign_id)
            memory = memory_of(interaction)
            if campaign is None or memory is None:
                return
            if interaction.user.id not in campaign.dm_user_ids:  # on every press
                await _tell(interaction, ONLY_DMS)
                return
            if shown is None:
                return
            now = await self._waiting(interaction)
            if shown[0].id not in {a.id for a, _ in now}:  # answered or gone meanwhile
                await self._show(interaction, now)
                return
            try:
                await memory.update_alias(
                    campaign.guild_id, campaign.id, shown[0].id,
                    source=DM, status=CONFIRMED if yes else REJECTED,
                )  # fmt: skip
            except MemoryRuleError as exc:
                log.info("A suggested name could not be answered: %s", exc)
                await _tell(interaction, "DMbot couldn't save that one. It stays waiting.")
                return
            if self.held is not None:
                self.held.forget(campaign.guild_id, shown[0].id)
            if not yes and self.spelling:
                await self._make_second_place(memory, campaign.guild_id, campaign.id, shown[0])
                self.separate += 1
            self.yes, self.no = self.yes + int(yes), self.no + int(not yes)
            await self._show(interaction, await self._waiting(interaction))

    async def _make_second_place(
        self, memory: MemoryStore, guild_id: int, campaign_id: str, alias: Alias
    ) -> None:
        """ "No, two places" (#3037): the turned-down spelling is a place of its own, waiting with
        the other new names. The no is remembered on the first place, so the pair is not asked
        again. A failure is logged: the answer is saved and the DM can add the place by hand."""
        try:
            await memory.add_entity(
                guild_id, campaign_id, type=place_spellings.PLACE, name=alias.text,
                source=BACKGROUND_SOURCE,
            )  # fmt: skip
        except MemoryRuleError as exc:
            log.info("The second place could not be made: %s", exc)
        except Exception:
            log.exception("The second place could not be made")

    async def _yes(self, interaction: discord.Interaction) -> None:
        await self._answer(True, interaction)

    async def _no(self, interaction: discord.Interaction) -> None:
        await self._answer(False, interaction)

    async def _not_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        campaign = await campaign_for(interaction, self.campaign_id)
        if campaign is None or interaction.user.id not in campaign.dm_user_ids:  # every press
            await _tell(interaction, ONLY_DMS)
            return
        self.left = len(await self._waiting(interaction))
        self.stop()
        with contextlib.suppress(discord.HTTPException):
            await interaction.edit_original_response(
                content=(
                    f"⏭️ Saved for later. {self.left} still waiting: press 📜 Check the "
                    "connections when you are ready."
                ),
                view=None,
                allowed_mentions=NO_PINGS,
            )


async def offer_names(interaction: discord.Interaction, campaign_id: str) -> bool:
    """The suggested other names, one at a time in a private message, before the connections.
    Returns whether there was anything to ask."""
    try:
        view = NameQuestions(campaign_id, getattr(_bot(interaction), "lore_context", None))
        if not await view.load(interaction):
            return False
        with contextlib.suppress(discord.HTTPException):
            await interaction.followup.send(
                view.text(), view=view, ephemeral=True, allowed_mentions=NO_PINGS
            )
        return True
    except Exception:
        log.exception("Couldn't offer the suggested names from a background")
        return False
