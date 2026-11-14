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
# The change log's snapshots of those rows get the same mark where they had none (as 0222 did), so
# an Undo across this still matches the live row (`_unchanged_since`) and never writes the old
# NULL back. A key the core kinds have taken since (a campaign's own `sworn_to` from before it was
# core, kept as replaced by the core one) is left out: the core kind wins (`Ontology.build`), and
# its default is the core's. That list is written here as it was today, so this migration never
# changes with later code.
#
# All three tables have forced row-level security: the backfill opens a policy on each table it
# reads or writes (CLAUDE.md, the lesson of #704 and #781) and closes them after.
CORE_KEYS = (
    "("
    "'advances', 'affected', 'ally_of', 'appears_in', 'associated_with', 'captive_of',"
    "'carries', 'contains', 'did', 'disguised_as', 'enemy_of', 'founded', 'in_thread',"
    "'kin_of', 'knows', 'leads', 'led_to', 'located_in', 'member_of', 'mentor_of',"
    "'next_to', 'of_people', 'owns', 'plays', 'promised_by', 'promised_to', 'resolves',"
    "'rival_of', 'route_to', 'serves', 'sworn_to', 'travels_with'"
    ")"
)

# The rows to mark, as one condition both statements use: the change log's first (it reads the
# rows while they are still NULL), then the rows.
_ROWS = (
    r"""
           r.both_ways IS NULL
           AND EXISTS (SELECT 1 FROM memory_predicates p
                        WHERE p.guild_id = r.guild_id AND p.campaign_id = r.campaign_id
                          AND p.key = r.predicate AND p.is_symmetric
                          AND p.key NOT IN """
    + CORE_KEYS
    + ")"
)

SQL = (
    r"""
    CREATE POLICY migrate_backfill ON memory_relations FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_predicates FOR ALL USING (true) WITH CHECK (true);
    CREATE POLICY migrate_backfill ON memory_changes FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_changes c SET
        before = CASE WHEN c.before IS NOT NULL
                       AND jsonb_typeof(COALESCE(c.before->'both_ways', 'null')) = 'null'
                      THEN jsonb_set(c.before, '{both_ways}', 'true') ELSE c.before END,
        after = CASE WHEN c.after IS NOT NULL
                      AND jsonb_typeof(COALESCE(c.after->'both_ways', 'null')) = 'null'
                     THEN jsonb_set(c.after, '{both_ways}', 'true') ELSE c.after END
      FROM memory_relations r
     WHERE c.guild_id = r.guild_id AND c.campaign_id = r.campaign_id
       AND c.table_name = 'memory_relations' AND c.row_id = r.id
       AND"""
    + _ROWS
    + r""";
    UPDATE memory_relations r SET both_ways = true WHERE"""
    + _ROWS
    + r""";
    DROP POLICY migrate_backfill ON memory_changes;
    DROP POLICY migrate_backfill ON memory_predicates;
    DROP POLICY migrate_backfill ON memory_relations;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()
