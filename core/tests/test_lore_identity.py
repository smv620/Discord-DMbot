"""A character known by an earlier name (#3034): the earlier name becomes an other name of the
character the campaign has, secret when the text calls it a disguise, and the lines written about
it reach that character. Never a new character. Invented names and text."""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED
from dmbot.ui import name_background as ui_bg
from dmbot.ui.name_lists import Upload
from tests import lore_fake
from tests.test_memory_names import GUILD, NamesTest

TEXT = (
    "Born Tamsin Vell, the thief fled north under the pseudonym Ash Morrow. Tamsin Vell served "
    "Master Orlen before the flight. Tamsin Vell is the sister of Wren Vell."
)
ANSWER = {
    "names": [
        {"name": "Ash Morrow", "kind": "person", "also": [], "secret": ["Tamsin Vell"],
         "quote": "under the pseudonym Ash Morrow"},
        {"name": "Master Orlen", "kind": "person", "also": [], "secret": [],
         "quote": "served Master Orlen"},
        {"name": "Wren Vell", "kind": "person", "also": [], "secret": [],
         "quote": "sister of Wren Vell"},
    ],
    "connections": [
        {"a": "Tamsin Vell", "how": "works for", "b": "Master Orlen",
         "quote": "Tamsin Vell served Master Orlen"},
        {"a": "Tamsin Vell", "how": "family", "b": "Wren Vell",
         "quote": "Tamsin Vell is the sister of Wren Vell"},
    ],
}  # fmt: skip


class TheEarlierName(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.store = BackstoryLinks(self.db)
        self.bot.backstory_links = self.store
        self.ai = lore_fake.lore_ai(json.dumps(ANSWER))
        self.bot.background_ai = self.bot.background_read_ai = self.ai  # type: ignore[assignment]

    async def ash(self) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Ash Morrow", type="character", role="player_character",
            played_by=1002, status=CONFIRMED, source="dm",
        )  # fmt: skip
        return str(made.value.id)

    async def go(self) -> Any:
        return await ui_bg.read_background(
            self.bot, self.campaign, Upload(TEXT, "your lore", True),
            truth=False, speaker=None, known_by="", started=time.monotonic(),
        )  # fmt: skip

    async def everyone(self) -> dict[str, Any]:
        found = await self.memory.entities(GUILD, self.campaign.id)
        return {e.name: e for e in found}

    async def test_the_earlier_name_is_a_secret_other_name_of_the_character_we_have(self) -> None:
        ash = await self.ash()
        await self.go()
        names = await self.everyone()
        self.assertNotIn("Tamsin Vell", names)  # (never a character of its own)
        found = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=ash, include_secret=True
        )
        self.assertEqual({(a.text, a.secret) for a in found if a.text != "Ash Morrow"},
                         {("Tamsin Vell", True)})  # fmt: skip
        # a suggestion for the DM, never confirmed on their behalf, and never shown to players
        (earlier,) = [a for a in found if a.text == "Tamsin Vell"]
        self.assertEqual(earlier.status, "proposed")
        shown = await self.memory.aliases(GUILD, self.campaign.id, entity_id=ash)
        self.assertNotIn("Tamsin Vell", {a.text for a in shown})

    async def test_what_is_written_about_the_earlier_name_reaches_the_character(self) -> None:
        ash = await self.ash()
        await self.go()
        waiting = await self.store.waiting(GUILD, self.campaign.id)
        about = {(w.predicate, w.object_id) for w in waiting if w.subject_id == ash}
        self.assertEqual(len(about), 2, about)  # the master and the sister: both from Ash Morrow
        every = await self.memory.entities(GUILD, self.campaign.id, statuses=["proposed"])
        names = {e.id: e.name for e in every}
        self.assertEqual({names[o] for _, o in about}, {"Master Orlen", "Wren Vell"})
        self.assertTrue(
            all(w.secret for w in waiting if w.subject_id == ash)
        )  # (through a disguise)

    async def test_the_same_text_read_twice_adds_nothing(self) -> None:
        await self.ash()
        await self.go()
        before = (
            len(await self.everyone()),
            len(await self.store.waiting(GUILD, self.campaign.id)),
        )
        await self.go()
        after = (len(await self.everyone()), len(await self.store.waiting(GUILD, self.campaign.id)))
        self.assertEqual(after, before)

    async def test_a_character_the_campaign_lacks_gets_the_earlier_name_with_its_entry(
        self,
    ) -> None:
        await self.go()
        names = await self.everyone()
        self.assertNotIn("Tamsin Vell", names)
        found = await self.memory.aliases(
            GUILD, self.campaign.id, entity_id=names["Ash Morrow"].id, include_secret=True
        )
        self.assertIn(("Tamsin Vell", True), {(a.text, a.secret) for a in found})

    async def test_a_name_two_entries_both_claim_is_saved_for_neither(self) -> None:
        from dmbot.ui.name_background import save_earlier_names

        a = await self.ash()
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name="Wren Vell", type="character", role="npc",
            status=CONFIRMED, source="dm",
        )  # fmt: skip
        await save_earlier_names(
            self.memory, self.campaign, {"Ash Morrow": ("The Fox",), "Wren Vell": ("The Fox",)}, ()
        )
        for entity in (a, made.value.id):
            found = await self.memory.aliases(
                GUILD, self.campaign.id, entity_id=entity, include_secret=True
            )
            self.assertNotIn("The Fox", {x.text for x in found})

    async def test_a_public_earlier_name_is_a_plain_suggestion(self) -> None:
        from dmbot.ui.name_background import save_earlier_names

        ash = await self.ash()
        await save_earlier_names(self.memory, self.campaign, {"Ash Morrow": ("Old Ash",)}, ())
        found = await self.memory.aliases(GUILD, self.campaign.id, entity_id=ash)
        (one,) = [a for a in found if a.text == "Old Ash"]
        self.assertEqual((one.secret, one.status), (False, "proposed"))

    async def test_an_earlier_name_with_a_line_of_its_own_is_folded_into_the_person(self) -> None:
        from dmbot.memory import lore_read

        _ = lore_read  # (the guard lives in read_lore: the fake reads below go through it)
        extra = {
            "names": [
                *ANSWER["names"],
                {"name": "Tamsin Vell", "kind": "person", "also": [], "secret": [],
                 "quote": "Born Tamsin Vell"},
            ],
            "connections": ANSWER["connections"],
        }  # fmt: skip
        self.ai = lore_fake.lore_ai(json.dumps(extra))
        self.bot.background_ai = self.bot.background_read_ai = self.ai  # type: ignore[assignment]
        await self.go()
        self.assertNotIn("Tamsin Vell", await self.everyone())
