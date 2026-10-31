"""0305_game_clock_log

An append-only log of the game clock (#3091, part 3).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

GAME_CLOCK_LOG = (
    """
    -- Every change to a campaign's game clock, kept for good: which clock (NULL is the main
    -- clock, 1 to 3 a split-party group), the real time (Unix seconds) and the game minute it
    -- moved to. A line spoken at a real time then has the game time of the newest row at or
    -- before it (`timebot/stamp.py`), so a belief formed from that line can be stamped from the
    -- line itself and never with the clock as it reads later. Written by a trigger on the two
    -- clock tables, so no way of changing a clock (a button, a phrase, Undo, a split, a rejoin)
    -- can miss it. Numbers only, never any words; one campaign's, deleted with it, in its backups,
    -- and following it when it moves to another server.
    CREATE TABLE game_clock_log (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER CHECK (position IS NULL OR position BETWEEN 1 AND 3),
        at          BIGINT NOT NULL CHECK (at > 0),
        minute      BIGINT NOT NULL CHECK (minute >= 0),
        PRIMARY KEY (guild_id, campaign_id, seq)
    );
    CREATE INDEX game_clock_log_by_time ON game_clock_log (guild_id, campaign_id, at, seq);
    """
    + _isolate("game_clock_log")
    + """
    -- Runs as whoever changed the clock, so the same server rules apply to the log row. A trigger
    -- failing makes the clock write fail with it (a future backfill of `minute` would need the
    -- `migrate_backfill` policy to open this table's row-level security too).
    -- A group's clock: a row whenever it is set or moves.
    CREATE FUNCTION game_clock_log_group() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
    BEGIN
        IF TG_OP = 'INSERT' OR NEW.minute IS DISTINCT FROM OLD.minute THEN
            INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)
            VALUES (NEW.guild_id, NEW.campaign_id, NEW.position,
                    floor(extract(epoch FROM clock_timestamp()))::BIGINT, NEW.minute);
        END IF;
        RETURN NULL;
    END
    $fn$;
    -- The main clock: a row when it is set or moves, and when the party splits or comes back
    -- together, so the log knows which clock the table was on. While split the main clock stands
    -- still (it is the clock from before the split), so its minute is not logged then. At a split
    -- every group's minute is logged again; at a rejoin or an Undo of a split the main clock's.
    CREATE FUNCTION game_clock_log_main() RETURNS trigger
        LANGUAGE plpgsql AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF TG_OP = 'INSERT' THEN
            INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)
            VALUES (NEW.guild_id, NEW.campaign_id, NULL, now_s, NEW.minute);
        ELSIF OLD.picked_group IS NULL AND NEW.picked_group IS NOT NULL THEN
            INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)
            SELECT g.guild_id, g.campaign_id, g.position, now_s, g.minute
              FROM game_clock_groups g
             WHERE g.guild_id = NEW.guild_id AND g.campaign_id = NEW.campaign_id
             ORDER BY g.position;
        ELSIF NEW.picked_group IS NULL
              AND (OLD.picked_group IS NOT NULL OR NEW.minute IS DISTINCT FROM OLD.minute) THEN
            INSERT INTO game_clock_log (guild_id, campaign_id, position, at, minute)
            VALUES (NEW.guild_id, NEW.campaign_id, NULL, now_s, NEW.minute);
        END IF;
        RETURN NULL;
    END
    $fn$;
    CREATE TRIGGER game_clocks_log AFTER INSERT OR UPDATE OF minute, picked_group ON game_clocks
        FOR EACH ROW EXECUTE FUNCTION game_clock_log_main();
    CREATE TRIGGER game_clock_groups_log AFTER INSERT OR UPDATE OF minute ON game_clock_groups
        FOR EACH ROW EXECUTE FUNCTION game_clock_log_group();
    """
)

SQL = GAME_CLOCK_LOG

ISOLATED_TABLES: tuple[str, ...] = ("game_clock_log",)
