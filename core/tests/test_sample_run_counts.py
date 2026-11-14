"""The sample run's two count lines (Supervisor, #3545): secret connections by source and the
split-party counts. Counts only, never a name. Invented names."""

from __future__ import annotations

import unittest

from dmbot.devtools.sample_run import counts
from dmbot.memory.models import CONFIRMED
from tests.test_memory_names import GUILD, NamesTest


class Lines(unittest.TestCase):
    def test_the_lines(self) -> None:
        got = counts.lines({"lore": 2, "dm": 1}, {"clock splits": 3})
        self.assertEqual(got[0], "Secret connections (confirmed): 3 (dm 1, lore 2)")
        self.assertTrue(got[1].startswith("Split party: clock splits 3, entries hidden from "))
        self.assertIn("private scenes not private 0", got[1])
        self.assertEqual(counts.lines({}, {})[0], "Secret connections (confirmed): 0 (none)")


class Reading(NamesTest):
    async def test_it_reads_a_campaign_and_names_nobody(self) -> None:
        cid = self.campaign.id
        a = await self.memory.add_entity(
            GUILD, cid, name="Sephek", type="character", status=CONFIRMED, source="dm"
        )
        b = await self.memory.add_entity(
            GUILD, cid, name="Torrga", type="character", status=CONFIRMED, source="dm"
        )
        await self.memory.add_relation(
            GUILD, cid, a.value.id, "knows", b.value.id, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        got = await counts.read(self.db, GUILD, cid)
        self.assertEqual(got[0], "Secret connections (confirmed): 1 (dm 1)")
        self.assertNotIn("Sephek", "\n".join(got))
