"""0223_transcript_erasures

A restore skips erased lines (#2605).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _isolate,
)

TRANSCRIPT_ERASURES = (
    """
    -- Who erased their transcripts in a server, and when (#2605). A backup made before that is
    -- restored without those lines: the person's "Delete my transcripts" must hold for a copy
    -- too. One row for each person and server (a later erase replaces the time). Ids and a time
    -- only, no words. A restore into another server cannot see this server's rows, so it asks
    -- through a door that answers for one server and the speakers named, and says nothing else.
    CREATE TABLE transcript_erasures (
        guild_id  BIGINT NOT NULL,
        user_id   BIGINT NOT NULL,
        erased_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    """
    + _isolate("transcript_erasures")
    + """
    CREATE FUNCTION dmbot_erased_speakers(g BIGINT, users BIGINT[])
        RETURNS TABLE (user_id BIGINT, erased_at BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.guild_id', true), '');
    BEGIN
        -- (the table's rows are isolated by server even for its owner: look as that server,
        -- for this one query, then as the caller's again)
        PERFORM set_config('dmbot.guild_id', g::TEXT, true);
        RETURN QUERY SELECT e.user_id, e.erased_at FROM transcript_erasures e
            WHERE e.guild_id = g AND e.user_id = ANY(users);
        PERFORM set_config('dmbot.guild_id', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_erased_speakers(BIGINT, BIGINT[])'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_erased_speakers(BIGINT, BIGINT[]) FROM PUBLIC;
"""
)

SQL = TRANSCRIPT_ERASURES

ISOLATED_TABLES: tuple[str, ...] = ("transcript_erasures",)
