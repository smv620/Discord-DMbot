"""0373_ai_feed

The AI feed (#3710): the first 500 characters of each prompt DMbot sends the AI during a lore read
or a session import, and of each reply, for the DM's live read-only windows.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

AI_FEED = (
    r"""
    -- One row per AI call of one lore upload or session import (`kind`, `job_id`), numbered `n`
    -- = 1, 2, 3... in the order the calls were written. The prompt and the reply are cut to
    -- their first 500 characters (then `[...]`) in Python before they are written, and the
    -- CHECKs refuse more: no longer text is ever stored. The row `n = 0` of a job is its
    -- bookkeeping, NOT a call (a reader shows `n > 0` only): `next_n` is the counter the numbers
    -- come from (one `INSERT .. ON CONFLICT DO UPDATE .. RETURNING`, so two calls at once never
    -- get one number), `lost` counts the calls whose write was skipped ("Some calls aren't
    -- shown"), and its `at` moves with the job so the sweep never takes it while the job goes
    -- on. DM-only everywhere: the website reads it only through a door that answers a DM or game
    -- master of the campaign. It holds prompt words (lore, a transcript, secret names) for a
    -- short time: a sweep deletes an entry 24 hours after it was written, "Stop recording me"
    -- deletes every row of the server at once, and a deleted campaign takes its rows. Never in a
    -- backup (campaigns/tables.py). The foreign key moves with a campaign (ON UPDATE CASCADE).
    CREATE TABLE ai_feed (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        kind        TEXT NOT NULL CHECK (kind IN ('lore', 'import')),
        job_id      TEXT NOT NULL CHECK (char_length(job_id) BETWEEN 1 AND 64),
        n           INTEGER NOT NULL CHECK (n >= 0),
        at          BIGINT NOT NULL CHECK (at > 0),
        service     TEXT NOT NULL DEFAULT '' CHECK (char_length(service) <= 40),
        model_label TEXT NOT NULL DEFAULT '' CHECK (char_length(model_label) <= 60),
        step        TEXT NOT NULL DEFAULT '' CHECK (char_length(step) <= 80),
        prompt      TEXT NOT NULL DEFAULT '' CHECK (char_length(prompt) <= 505),
        reply       TEXT CHECK (char_length(reply) <= 505),
        state       TEXT NOT NULL CHECK (state IN ('waiting', 'done', 'failed')),
        reason      TEXT CHECK (char_length(reason) <= 200),
        next_n      INTEGER NOT NULL DEFAULT 0 CHECK (next_n >= 0),
        lost        INTEGER NOT NULL DEFAULT 0 CHECK (lost >= 0),
        PRIMARY KEY (guild_id, campaign_id, kind, job_id, n)
    );
    CREATE INDEX ai_feed_age ON ai_feed (at);
    """
    + _isolate("ai_feed")
    + r"""
    -- "Stop recording me" empties the server's feed (#3710). The website's Stop route runs
    -- ConsentStore.revoke as the signed-in person, whose role has no right on the table, so the
    -- delete goes through this one function: it deletes this server's rows and nothing else, and
    -- answers how many. The bot (no signed-in person) may call it for the server it is working in;
    -- a signed-in person only for a server on their own list.
    CREATE FUNCTION dmbot_ai_feed_forget(g BIGINT) RETURNS INTEGER
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        gone INTEGER;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR (dmbot_current_user() IS NOT NULL
               AND NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))) THEN
            RETURN 0;
        END IF;
        DELETE FROM ai_feed WHERE guild_id = g;
        GET DIAGNOSTICS gone = ROW_COUNT;
        RETURN gone;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_ai_feed_forget(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_ai_feed_forget(BIGINT) FROM PUBLIC;
    """
)

SQL = AI_FEED

ISOLATED_TABLES: tuple[str, ...] = ("ai_feed",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_ai_feed_forget", "dmbot_ai_feed_forget(BIGINT)"),
)
