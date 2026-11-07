"""0382_made_by_commit_rest

Data lineage, part 4 (#3739): the rest of what DMbot generates records the build that made it.
"""

from __future__ import annotations

# Part 4 of #3739 (see `dmbot.lineage` and 0378, which made the trigger function). Nothing here
# changes a row: the column is NULL for everything made before this. Stamped: the AI feed, the
# words a map was put into, PlotBot's checks, issues and talk, the offers to set an import's clock,
# the character sheet snapshots DMbot reads from D&D Beyond and the spelling rules (a reader may
# write them). Not stamped (see `lineage.NOT_STAMPED`): posted pictures (a person's), the picture
# queue, the DM's "not a problem" notes and the suggestions a DM sends to a pack or a set.
TABLES = (
    "ai_feed",
    "map_notes",
    "plot_checks",
    "plot_issues",
    "plot_chat",
    "import_clock_offers",
    "character_sheets",
    "memory_corrections",
)

SQL = (
    r"""
    DO $$
    DECLARE
        t TEXT;
    BEGIN
        FOREACH t IN ARRAY ARRAY["""
    + ", ".join(f"'{t}'" for t in TABLES)
    + r"""] LOOP
            EXECUTE format(
                'ALTER TABLE %I ADD COLUMN made_by_commit TEXT'
                ' CONSTRAINT %I CHECK (made_by_commit ~ ''^[0-9a-f]{40}$'')',
                t, t || '_made_by_commit_check');
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON %I FOR EACH ROW'
                ' EXECUTE FUNCTION dmbot_stamp_commit()',
                t || '_stamp_commit', t);
        END LOOP;
    END $$;
"""
)
