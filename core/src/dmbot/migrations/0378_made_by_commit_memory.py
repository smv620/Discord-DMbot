"""0378_made_by_commit_memory

Data lineage, part 1 (#3739): story memory records the build that made each row.
"""

from __future__ import annotations

# The owner's rule (2026-11-07): a fault found later must be pinned to the version that made the
# row, so an old row from a build that is already fixed is not taken for a new bug. See
# `dmbot.lineage` for what the stamp means and how it is attached. Nothing here changes a row: the
# column is NULL for everything made before this ("made before versions were recorded"), so no
# backfill and no row-level-security opening is needed.
#
# `dmbot_stamp_commit()` is the one trigger every stamped table in every part uses:
# - INSERT takes the running build's commit (`dmbot.commit`, set by `Database` in each transaction);
#   unless `dmbot.restoring` is on (a backup load), when the row keeps what it carries.
# - UPDATE keeps the stored stamp, so a DM's own edit never changes it; it takes the running
#   build's commit only when DMbot is rewriting its own work (`dmbot.rewrites`).
# An unknown build (no `dmbot.commit`) stamps NULL, never text. The function does not read or
# write any other row, so it needs no definer rights.
#
# The stamped tables here are the story-memory ones whose rows DMbot makes or rewrites (agreed with
# GraphEngineer on #3739). Left out on purpose: `memory_types`, `memory_predicates` (the campaign's
# own kinds), `name_later`, `name_not_same`, `name_answers`, `name_actions` (the DM's answers and
# queues). Later parts add recaps and audio, transcripts and imports, then the rest.
TABLES = (
    "memory_entities",
    "memory_aliases",
    "memory_relations",
    "memory_claims",
    "memory_states",
    "memory_perspectives",
    "memory_beliefs",
    "memory_evidence",
    "memory_impressions",
    "memory_attitudes",
    "memory_deed_proposals",
    "memory_scenes",
    "memory_changes",
    "memory_contradictions",
    "memory_differences",
    "memory_heard",
    "memory_mentions",
    "memory_vectors",
    "memory_who_answers",
    "memory_kind_offers",
    "memory_knowers",
    "memory_alias_knowers",
    "memory_told",
    "memory_link_sources",
    "memory_importance",
    "memory_flags",
    "memory_rule_links",
    "memory_report_runs",
    "backstory_links",
    "backstory_which",
    "waiting_name_questions",
    "name_pass_fixes",
    "name_pass_reports",
    "rel_ties",
    "lore_uploads",
)

SQL = (
    r"""
    CREATE FUNCTION dmbot_stamp_commit() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    DECLARE
        running TEXT := NULLIF(current_setting('dmbot.commit', true), '');
    BEGIN
        IF current_setting('dmbot.restoring', true) = 'on' THEN
            RETURN NEW;
        ELSIF TG_OP = 'INSERT' THEN
            NEW.made_by_commit := running;
        ELSIF current_setting('dmbot.rewrites', true) = 'on' THEN
            NEW.made_by_commit := running;
        ELSE
            NEW.made_by_commit := OLD.made_by_commit;
        END IF;
        RETURN NEW;
    END
    $fn$;

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
