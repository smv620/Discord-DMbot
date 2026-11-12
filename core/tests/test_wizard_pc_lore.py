"""A player character the wizard made and the lore's first name for them are one entry (#4224): the
lore read gives the character its first name before it looks (#3880's rule, run as the session
scan does), so "Cerric" is never a copy of Cerric Vanya'elen and the lore's ties stay on the DM's
own character. Invented names and text."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock

from dmbot import lore_upload
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.lore_queue import LoreQueue
from dmbot.memory.models import CONFIRMED, DM, PLAYER_CHARACTER, name_key
from dmbot.ui.name_lists import _ai_busy, _ai_reads
from tests import lore_fake
from tests.test_memory_names import DM as DM_USER
from tests.test_memory_names import GUILD, NamesTest

TEXT = (
    "Cerric Vanya'elen came down from the northern woods. Years ago, Cerric's brother Ulric Vane "
    "left home to join the river guard."
)
ANSWER: dict[str, Any] = {
    "names": [
        {"name": "Cerric", "kind": "npc", "quote": "Cerric's brother Ulric Vane"},
        {"name": "Ulric Vane", "kind": "npc", "quote": "Cerric's brother Ulric Vane"},
    ],
    "connections": [
        {"a": "Cerric", "how": "family", "b": "Ulric Vane", "quote": "Cerric's brother Ulric Vane"}
    ],
}


class TheWizardsCharacter(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _ai_busy.clear()
        _ai_reads.clear()
        ai = lore_fake.lore_ai(json.dumps(ANSWER))
        self.bot.background_ai = ai  # type: ignore[assignment]
        self.bot.background_read_ai = ai  # type: ignore[assignment]
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.campaigns.record_confirmation = AsyncMock()  # type: ignore[method-assign]
        self.bot.backstory_links = BackstoryLinks(self.db)
        self.queue = LoreQueue(self.db)
        made = await self.memory.add_entity(  # as the wizard's party step makes it
            GUILD, self.campaign.id, type="character", name="Cerric Vanya'elen", source=DM,
            status=CONFIRMED, role=PLAYER_CHARACTER,
        )  # fmt: skip
        self.cerric = made.value.id

    async def asyncTearDown(self) -> None:
        _ai_busy.clear()
        _ai_reads.clear()
        await super().asyncTearDown()

    async def read(self) -> None:
        now = int(time.time())
        async with self.db.guild(GUILD) as conn:  # (a pasted lore upload, as the wizard queues)
            await conn.execute(
                "INSERT INTO lore_uploads (guild_id, campaign_id, id, user_id, source, text, link,"
                " choice, rights_by, rights_at, created_at, updated_at, state) VALUES"
                " (%s, %s, replace(gen_random_uuid()::text, '-', ''), %s, 'paste', %s, '',"
                " 'world', %s, %s, %s, %s, 'waiting')",
                (GUILD, self.campaign.id, DM_USER, TEXT, DM_USER, now, now, now),
            )
        claim = await self.queue.claim_next(GUILD)
        assert claim is not None
        await lore_upload.run_upload(self.bot, self.queue, claim, GUILD)

    async def test_the_first_name_is_the_characters_never_a_copy(self) -> None:
        await self.read()
        named_cerric = [
            e
            for e in await self.memory.entities(GUILD, self.campaign.id)
            if name_key(e.name) == "cerric"
        ]
        self.assertEqual(named_cerric, [])  # no second Cerric
        aliases = await self.memory.aliases(GUILD, self.campaign.id, entity_id=self.cerric)
        self.assertIn("cerric", {name_key(a.text) for a in aliases})

    async def test_the_lores_tie_reaches_the_character(self) -> None:
        await self.read()
        ulric = next(
            e.id
            for e in await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
            if e.name == "Ulric Vane"
        )
        links = self.bot.backstory_links
        assert links is not None
        waiting = await links.waiting(GUILD, self.campaign.id)
        ends = {frozenset((w.subject_id, w.object_id)) for w in waiting}
        kept = {
            frozenset((r.subject_id, r.object_id))
            for r in await self.memory.relations(GUILD, self.campaign.id, entity_id=self.cerric)
        }
        self.assertIn(frozenset((self.cerric, ulric)), ends | kept)
