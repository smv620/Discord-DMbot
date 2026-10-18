"""The name forms and Undo answer Discord before their reads and saves (#1803), by order."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import discord

from dmbot.campaigns.models import Campaign
from dmbot.memory.models import MERGED, Entity
from dmbot.ui import name_card, names
from tests.answer_first import Timeline

GUILD, DM = 1, 7
CAMPAIGN = Campaign(
    "c" * 32, GUILD, "Rime", 0, None, "2024", "2014", True, frozenset({DM}), None, None, "peek"
)


def interaction(line: Timeline, **client: Any) -> Any:
    member = MagicMock(spec=discord.Member)
    member.id = DM
    member.guild_permissions.manage_guild = True
    it = SimpleNamespace(
        guild=SimpleNamespace(id=GUILD),
        guild_id=GUILD,
        user=member,
        type=discord.InteractionType.modal_submit,
        response=line.response,
        followup=line.followup,
        edit_original_response=line.edit_original_response,
        client=SimpleNamespace(**client),
    )
    return it


class FormsAnswerFirst(unittest.IsolatedAsyncioTestCase):
    def campaigns(self, line: Timeline) -> Any:
        return SimpleNamespace(get=line.slow("campaign", CAMPAIGN))

    async def test_add_a_name_answers_before_the_campaign_and_name_reads(self) -> None:
        line = Timeline()
        memory = SimpleNamespace(confirmed_name_for=line.slow("known", None))
        it = interaction(line, campaigns=self.campaigns(line), memory=memory)
        form = names.AddNameForm(CAMPAIGN.id, secrets=False)
        form.name._value = "Belleros"
        form.others._value = ""
        await form.on_submit(it)
        line.assert_answered_first()
        self.assertIn("slow:known", line.events)

    async def test_add_a_character_answers_before_the_save(self) -> None:
        line = Timeline()
        it = interaction(line, campaigns=self.campaigns(line), memory=SimpleNamespace())
        form = names.CharacterForm(CAMPAIGN.id, 9, "Mia")
        form.name._value = "Cerric"
        form.others._value = ""
        form.sheet._value = ""
        saved = Entity("e" * 32, "pc", "Cerric", "", "confirmed", None, "dm", 0)
        with (
            patch.object(names, "save_name", line.slow("save", saved)),
            patch.object(names, "changed"),
        ):
            await form.on_submit(it)
        line.assert_answered_first()
        self.assertIn("slow:save", line.events)

    async def test_undo_answers_before_its_reads(self) -> None:
        line = Timeline()
        entity = Entity("e" * 32, "npc", "Belleros", "", MERGED, None, "dm", 0)
        memory = SimpleNamespace(entity=line.slow("entity", entity), undo=line.slow("undo", None))
        it = interaction(line, campaigns=self.campaigns(line), memory=memory)
        it.type = discord.InteractionType.component
        with patch.object(name_card, "changed"), patch.object(name_card, "show_card"):
            await name_card.UndoButton(CAMPAIGN.id, entity.id, 3).callback(it)
        line.assert_answered_first()
        self.assertIn("slow:campaign", line.events)
