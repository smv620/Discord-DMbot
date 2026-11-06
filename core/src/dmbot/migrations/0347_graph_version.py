"""0347_graph_version

A door that returns only the Story Graph's version number (#3294).
"""

from __future__ import annotations

# The Story Graph page re-reads itself every few seconds while its tab is open, and each read is a
# full graph read. `campaigns.memory_version` is bumped in the same transaction as every graph
# write (the one write chokepoint, GraphEngineer's design note on #3294), so the page can ask for
# this number first and read the whole graph only when it, or the viewer, has changed. The door
# returns the number and nothing else, for members of the server only, as the graph doors do
# (`dmbot_web_graph_source`): anyone else gets NULL. A player who polls it learns only that
# something changed, never what; the full read they then make shows only what they may see.
# Nothing is written.
GRAPH_VERSION = r"""
    CREATE FUNCTION dmbot_web_graph_version(g BIGINT, c TEXT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        v BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT memory_version INTO v FROM campaigns WHERE guild_id = g AND id = c;
        RETURN v;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_graph_version(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_graph_version(BIGINT, TEXT) FROM PUBLIC;
"""

SQL = GRAPH_VERSION

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_graph_version", "dmbot_web_graph_version(BIGINT, TEXT)"),
)
