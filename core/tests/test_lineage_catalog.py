"""Every table that holds a campaign's rows is stamped, or on a written list (#3739).

The same catalog idea as `test_campaign_tables`: a table that is on none of the lists is a table
whose rows nobody decided about, so a fault in them could not be pinned to a build. The lists are
in `dmbot.lineage`: `STAMPED` (has the column and the trigger), `NOT_YET` (a later part of #3739
adds it; each part shortens this list) and `NOT_STAMPED` (the DM's own or plumbing, with why).
"""

from __future__ import annotations

from dmbot import lineage
from tests.pg import DatabaseTest
from tests.test_campaign_tables import BELONGING

GUILD = 1


class Catalog(DatabaseTest):
    maxDiff = None

    async def _names(self, query: str) -> set[str]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(query)
            return {str(r["name"]) for r in await cur.fetchall()}

    async def test_the_stamp_is_on_exactly_the_stamped_tables_and_each_has_its_trigger(
        self,
    ) -> None:
        have = await self._names(
            "SELECT table_name::text AS name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND column_name = 'made_by_commit'"
        )
        self.assertEqual(have, set(lineage.STAMPED))
        triggered = await self._names(
            "SELECT c.relname::text AS name FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
            " JOIN pg_proc p ON p.oid = t.tgfoid WHERE p.proname = 'dmbot_stamp_commit'"
            " AND NOT t.tgisinternal"
        )
        self.assertEqual(triggered, set(lineage.STAMPED))

    async def test_every_campaign_table_is_stamped_or_on_a_written_list(self) -> None:
        belonging = await self._names(BELONGING)
        listed = set(lineage.STAMPED) | set(lineage.NOT_YET) | set(lineage.NOT_STAMPED)
        self.assertEqual(sorted(belonging - listed), [], "decide these: stamp or write why not")
        self.assertEqual(sorted(listed - belonging - set(lineage.STAMPED)), [], "stale entries")
        self.assertFalse(set(lineage.NOT_YET) & set(lineage.NOT_STAMPED))
        self.assertFalse(set(lineage.STAMPED) & (set(lineage.NOT_YET) | set(lineage.NOT_STAMPED)))
