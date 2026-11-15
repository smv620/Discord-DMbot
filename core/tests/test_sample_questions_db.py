"""Part 2's snapshot reads the real tables (#3545): what waits is found in the one campaign only,
and a name the DM keeps as DMbot named it counts as "kept as suggested". Invented names. Needs
Postgres."""

from __future__ import annotations

from dmbot.campaigns import CampaignStore
from dmbot.devtools.sample_run import questions as q
from dmbot.memory.models import DM, PROPOSED
from tests.test_memory_store import DM as DM_USER
from tests.test_memory_store import GUILD_A, MemoryTest


class Snapshot(MemoryTest):
    async def test_a_name_kept_as_named_is_dmbots_right_guess(self) -> None:
        kept = await self.add("Tamsin Vell", status=PROPOSED, source="background")
        await self.add("Oro", status=PROPOSED, source="background")
        other = (await CampaignStore(self.db).create(GUILD_A, "Elsewhere", DM_USER)).id
        await self.add("Brin", campaign=other, status=PROPOSED, source="background")
        before = await q.waiting(self.db, self.memory, GUILD_A, self.c)
        self.assertEqual(len(before[q.NAMES]), 2)  # (never the other campaign's)
        await self.memory.confirm_entity(GUILD_A, self.c, kept, "npc", source=DM)
        got = q.classify(before, await q.read_state(self.db, GUILD_A, self.c))
        self.assertEqual(dict(got[q.NAMES]), {q.KEPT: 1, q.OPEN: 1})

    async def test_the_questions_asked_are_the_bots_own_count(self) -> None:
        from dmbot.memory.backstory_links import BackstoryLinks
        from dmbot.memory.claims import ClaimStore
        from dmbot.questions import counts

        await self.add("Oro", status=PROPOSED, source="background")
        sources = counts.Sources(
            db=self.db, memory=self.memory, claims=ClaimStore(self.memory),
            backstory=BackstoryLinks(self.db),
        )  # fmt: skip
        got = await q.asked(sources, GUILD_A, self.c)
        self.assertEqual(got.get("New names to check"), 1)
