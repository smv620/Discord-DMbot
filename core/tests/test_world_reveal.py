"""A World read reveals what it states plainly (#4524 slice 1b, Supervisor's ruling C): a hidden
entry it names word for word, a hidden connection it states itself, and a secret other name only
where the text gives it on that same person's own line. A smart or spoiler read reveals nothing;
the upload's ↩️ Undo takes it back; the notice gives a count only. Invented names and text."""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot.memory import lore_read, world_reveal
from dmbot.memory.backstory_links import BackstoryLinks
from dmbot.memory.models import CONFIRMED, DM
from dmbot.ui import name_background as ui_bg
from dmbot.ui import upload_notice as un
from dmbot.ui.name_lists import Upload
from dmbot.upload_notices import UploadNotices
from tests import lore_fake
from tests.test_memory_names import DM as THE_DM
from tests.test_memory_names import GUILD, NamesTest

TEXT = "Ash Morrow served Master Orlen in the old days."
NAMES = [
    {"name": "Ash Morrow", "kind": "person", "also": [], "secret": [], "quote": "Ash Morrow"},
    {"name": "Master Orlen", "kind": "person", "also": [], "secret": [], "quote": "Master Orlen"},
]
SERVED = {"a": "Ash Morrow", "how": "works for", "b": "Master Orlen",
          "quote": "Ash Morrow served Master Orlen"}  # fmt: skip


class Pure(unittest.TestCase):
    def test_a_name_counts_only_where_the_text_holds_it_word_for_word(self) -> None:
        plain = "the ashen crown met cerric vanyaelen in brynshander"
        self.assertTrue(lore_read._says_word_for_word(plain, "Cerric Vanya'elen"))
        self.assertFalse(lore_read._says_word_for_word(plain, "Cerric Vane"))
        self.assertFalse(lore_read._says_word_for_word(plain, "Ash"))  # (not part of "ashen")

    def test_only_what_may_be_shown_is_shown(self) -> None:
        base = {"status": CONFIRMED, "hidden_from_players": True, "type": "character",
                "role": "npc", "played_by": None, "audience_limited": False,
                "party": False}  # fmt: skip
        self.assertTrue(world_reveal.may_show(SimpleNamespace(**base)))
        for change in ({"audience_limited": True}, {"party": True}, {"type": "clue"},
                       {"role": "player_character"}, {"hidden_from_players": False},
                       {"status": "proposed"}):  # fmt: skip
            with self.subTest(change):
                self.assertFalse(world_reveal.may_show(SimpleNamespace(**{**base, **change})))

    def test_the_notice_counts_and_never_names(self) -> None:
        text = un.notice_text("the city guide, 3 Nov", {"revealed": 3}, 0)
        self.assertIn("🌍 **3 things** from backstories are now known to everyone.", text)
        one = un.notice_text("the city guide, 3 Nov", {"revealed": 1}, 0)
        self.assertIn("**1 thing** from backstories is now known", one)


class TheRead(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaign = await self.campaigns.set_ask_before_adding(GUILD, self.campaign.id, False)
        self.bot.plan_gate = AsyncMock(return_value=None)  # type: ignore[method-assign]
        self.bot.backstory_links = BackstoryLinks(self.db)
        self.bot.upload_notices = UploadNotices(self.db)
        self.bot.lookup = None
        self.ash = await self.npc("Ash Morrow")
        self.orlen = await self.npc("Master Orlen")

    async def npc(self, name: str) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type="character", role="npc",
            status=CONFIRMED, source=DM,
        )  # fmt: skip
        return str(made.value.id)

    async def hide(self, entity_id: str) -> None:
        await self.memory.set_hidden_from_players(
            GUILD, self.campaign.id, entity_id, True, source=DM, dm_chose_secret=True
        )

    async def secret_tie(self) -> str:
        made = await self.memory.add_relation(
            GUILD, self.campaign.id, self.ash, "serves", self.orlen, source=DM,
            confidence=1.0, status=CONFIRMED, secret=True, dm_chose_secret=True,
        )  # fmt: skip
        relation = made.value[0] if isinstance(made.value, tuple) else made.value
        return str(relation.id)

    async def read(
        self, choice: str, names: Any = NAMES, links: Any = (SERVED,), text: str = TEXT
    ) -> Any:
        answer = {"names": list(names), "connections": list(links)}
        ai = lore_fake.lore_ai(json.dumps(answer))
        self.bot.background_ai = self.bot.background_read_ai = ai  # type: ignore[assignment]
        return await ui_bg.read_background(
            self.bot, self.campaign, Upload(text, "the city guide", True),
            truth=False, speaker=None, known_by="", started=time.monotonic(), choice=choice,
        )  # fmt: skip

    async def hidden(self, entity_id: str) -> bool:
        e = await self.memory.entity(GUILD, self.campaign.id, entity_id)
        assert e is not None
        return e.hidden_from_players

    async def tie_secret(self, relation_id: str) -> bool:
        rows = await self.memory.relations(GUILD, self.campaign.id, include_secret=True)
        return next(r for r in rows if r.id == relation_id).secret

    async def test_a_world_read_shows_a_hidden_name_it_gives_and_undo_takes_it_back(self) -> None:
        await self.hide(self.ash)
        done = await self.read("world")
        self.assertFalse(await self.hidden(self.ash))
        common = await self.memory.common_knowledge(GUILD, self.campaign.id, "entry", [self.ash])
        self.assertEqual(common, {self.ash})
        self.assertEqual(done.notice.counts.get("revealed"), 1)
        await un.undo_upload_result(self.bot, GUILD, self.campaign.id, done.notice.id, THE_DM)
        self.assertTrue(await self.hidden(self.ash))

    async def test_a_smart_or_spoiler_read_reveals_nothing(self) -> None:
        await self.hide(self.ash)
        tie = await self.secret_tie()
        for choice in ("smart", "spoilers"):
            with self.subTest(choice):
                await self.read(choice)
                self.assertTrue(await self.hidden(self.ash))
                self.assertTrue(await self.tie_secret(tie))

    async def test_a_world_read_reveals_a_connection_it_states(self) -> None:
        tie = await self.secret_tie()
        done = await self.read("world")
        self.assertFalse(await self.tie_secret(tie))
        self.assertEqual(done.notice.counts.get("revealed"), 1)

    async def test_a_connection_written_through_a_secret_name_stays_secret(self) -> None:
        await self.memory.add_alias(
            GUILD, self.campaign.id, self.ash, "the hooded stranger", kind="title", secret=True,
            status=CONFIRMED, source=DM, dm_chose_secret=True,
        )  # fmt: skip
        tie = await self.secret_tie()
        hooded = {"a": "the hooded stranger", "how": "works for", "b": "Master Orlen",
                  "quote": "the hooded stranger served Master Orlen"}  # fmt: skip
        await self.read("world", links=(hooded,),
                        text="The hooded stranger served Master Orlen.")  # fmt: skip
        self.assertTrue(await self.tie_secret(tie))

    async def test_a_secret_name_on_its_own_persons_line_is_revealed_but_not_alone(self) -> None:
        made = await self.memory.add_alias(
            GUILD, self.campaign.id, self.ash, "Tamsin Vell", kind="old", secret=True,
            status=CONFIRMED, source=DM, dm_chose_secret=True,
        )  # fmt: skip
        alias = made.value.id

        async def secret() -> bool:
            names = await self.memory.aliases(
                GUILD, self.campaign.id, entity_id=self.ash, include_secret=True
            )
            return next(a for a in names if a.id == alias).secret

        alone = [{"name": "Tamsin Vell", "kind": "person", "also": [], "secret": [],
                  "quote": "Tamsin Vell"}]  # fmt: skip
        await self.read("world", names=alone, links=(), text="Tamsin Vell fled north.")
        self.assertTrue(await secret())  # (the same name could be someone else's)
        own = [{"name": "Ash Morrow", "kind": "person", "also": ["Tamsin Vell"], "secret": [],
                "quote": "Ash Morrow, born Tamsin Vell"}]  # fmt: skip
        await self.read("world", names=own, links=(), text="Ash Morrow, born Tamsin Vell, fled.")
        self.assertFalse(await secret())

    async def test_a_name_the_text_does_not_hold_is_never_revealed(self) -> None:
        await self.hide(self.ash)
        await self.read("world", links=(), text="Master Orlen kept the old ways.")
        self.assertTrue(await self.hidden(self.ash))


if __name__ == "__main__":
    unittest.main()
