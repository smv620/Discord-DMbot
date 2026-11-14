"""0485_own_kind_both_ways

A campaign's own two-way connection kind: every row says so (#4388, a follow-up to #4354).
"""

from __future__ import annotations

# A kind's default direction lives in two places: the core kinds' list in code
# (`link_words.BOTH_WAYS`), which the card, the website's door and Discord's direction menu read,
# and a campaign's own kinds' table (`memory_predicates.is_symmetric`), which the search text and
# the stored order read. So a row of a campaign's own two-way kind with no mark of its own (NULL,
# "its kind's default") read one way on the card and two way in search, and the same line said again
# after the DM turned it was not taken for its twin. From now on `add_relation_in` writes the mark
# (true) on such a row, and this backfill gives it to the rows made before. A row the DM set
# (true or false, #4154 and #4354) is left as it is. The stored order of these rows is already the
# two-way one (`checks.ordered` read `is_symmetric`), so nothing else moves.
#
# Both tables have forced row-level security: the backfill opens a policy on each table it reads
# (CLAUDE.md, the lesson of #704 and #781) and closes them after.
SQL = r"""
    CREATE POLICY migrate_backfill ON memory_relations FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_predicates FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_relations r SET both_ways = true
     WHERE r.both_ways IS NULL
       AND EXISTS (SELECT 1 FROM memory_predicates p
                    WHERE p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                      AND p.key = r.predicate AND p.is_symmetric);
    DROP POLICY migrate_backfill ON memory_predicates;
    DROP POLICY migrate_backfill ON memory_relations;
    """

ISOLATED_TABLES: tuple[str, ...] = ()
