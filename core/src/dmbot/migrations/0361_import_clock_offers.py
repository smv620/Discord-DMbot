"""0361_import_clock_offers

The website's half of the import clock offer (#3199, the page is #3262): the game minute an
imported session ended at and what became of the offer to set the clock there, a DM-only read door
for it, and two queued name actions, `clock-set` and `clock-skip`.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row for each imported session that was offered (or refused, for a split party) a clock
# change: the game minute and the state. `offered` waits for a DM; `applying` is the moment one
# press holds it while the clock moves, so two presses (the website's and Discord's) cannot both
# set it (it reads as `offered`, or as `expired` if the bot died then); `set`, `dismissed`
# (Not now),
# `moved_on` (the clock was already at or past it when the DM pressed) and `split` (the party was
# split) are the ends; `expired` is an offer a newer one replaced. An offer older than seven days
# reads as expired without a write (the read door says so). A minute and a code only: never a word
# of the session. Written by the bot where Discord's offer is made, and by Discord's buttons, so
# the two places never disagree. A pending offer is not campaign content and means nothing after a
# restore, so it is not carried in a backup (campaigns/tables.py).
TABLE = r"""
    CREATE TABLE import_clock_offers (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        session_id  TEXT NOT NULL,
        FOREIGN KEY (session_id, guild_id)
            REFERENCES transcript_sessions (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        minute      BIGINT NOT NULL CHECK (minute > 0),
        state       TEXT NOT NULL CHECK (state IN
                        ('offered', 'applying', 'set', 'dismissed', 'moved_on', 'split',
                         'expired')),
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        updated_at  BIGINT NOT NULL CHECK (updated_at > 0),
        PRIMARY KEY (guild_id, campaign_id, session_id)
    );
    CREATE INDEX import_clock_offers_open ON import_clock_offers (guild_id, campaign_id, state);
""" + _isolate("import_clock_offers")

# The offers of one import, for the DM of the campaign only (anyone else gets NULL), by the file
# name each session was imported under. `sessionId` is what a choice is queued against. Only
# codes, ids and the minute leave: the page words the label.
READ_DOOR = r"""
    CREATE FUNCTION dmbot_web_import_clocks(g BIGINT, c TEXT, imp TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'file', si.file_name, 'sessionId', o.session_id, 'minute', o.minute,
                       'state', CASE
                           WHEN o.state = 'offered' AND o.created_at < now_s - 604800
                               THEN 'expired'
                           WHEN o.state = 'applying' AND o.updated_at < now_s - 600
                               THEN 'expired'
                           WHEN o.state = 'applying' THEN 'offered'
                           ELSE o.state END,
                       'updatedAt', o.updated_at) ORDER BY si.imported_at, si.file_name)
            FROM session_imports si
            JOIN import_clock_offers o ON o.guild_id = si.guild_id
                 AND o.campaign_id = si.campaign_id AND o.session_id = si.session_id
            WHERE si.guild_id = g AND si.campaign_id = c AND si.import_id = imp
        ), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_import_clocks(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_import_clocks(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

# The queue door is read back from the database as it is (many migrations have changed it) and
# each anchor must be found the exact number of times, so a drift stops the migration instead of
# widening the wrong rule, as 0354 and 0355 do. The anchors are the ends of the lists, so an
# action another migration adds to a list does not move them.
_SWAPS = (
    # the allowed actions
    (
        "')\n        THEN\n            RETURN jsonb_build_object('status', 'bad_action');",
        "', 'clock-set', 'clock-skip')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        1,
    ),
    # no text with either
    (
        "')\n               AND clean <> '')",
        "', 'clock-set', 'clock-skip')\n               AND clean <> '')",
        1,
    ),
    # both need a target (the imported session's id) ...
    (
        "')\n               AND (tgt IS NULL",
        "', 'clock-set', 'clock-skip')\n               AND (tgt IS NULL",
        1,
    ),
    # ... and no other action takes one
    (
        "')\n               AND COALESCE(tgt, '') <> '')",
        "', 'clock-set', 'clock-skip')\n               AND COALESCE(tgt, '') <> '')",
        1,
    ),
    # no flag
    (
        "')\n               AND COALESCE(fl, false))\n           OR (act IN ('link-words'",
        "', 'clock-set', 'clock-skip')\n               AND COALESCE(fl, false))\n"
        "           OR (act IN ('link-words'",
        1,
    ),
    # neither is filed under a name (the way the Add all actions are not)
    (
        "OR act IN ('confirm-kinds', 'confirm-undo') THEN\n            -- a waiting connection",
        "OR act IN ('confirm-kinds', 'confirm-undo', 'clock-set', 'clock-skip') THEN\n"
        "            -- a waiting connection",
        1,
    ),
    # the offer must still be waiting for a DM in this campaign (the bot checks again)
    (
        "        IF act = 'alias-who' AND NOT EXISTS (",
        "        IF act IN ('clock-set', 'clock-skip') AND NOT EXISTS (\n"
        "               SELECT 1 FROM import_clock_offers o\n"
        "               WHERE o.guild_id = g AND o.campaign_id = c AND o.session_id = tgt\n"
        "                 AND o.state = 'offered'\n"
        "                 AND o.created_at >= now_s - 604800) THEN\n"
        "            RETURN jsonb_build_object('status', 'not_found');\n"
        "        END IF;\n"
        "        IF act = 'alias-who' AND NOT EXISTS (",
        1,
    ),
)


def _text(text: str) -> str:
    """The text as SQL that builds it from hex. The anchors above end or start in the middle of a
    quoted word, and a text written between quotes would not read as balanced SQL to the checks
    that scan the migrations (`test_migration_backfill`); the hex has no quote in it."""
    return f"convert_from(decode('{text.encode().hex()}', 'hex'), 'UTF8')"


_STEPS = "".join(
    f"\n        def := pg_temp.swap_n(def, {_text(old)}, {_text(new)}, {n});"
    for old, new, n in _SWAPS
)

SQL = (
    TABLE
    + r"""
    -- The allowed actions, read back from the table as it is and two more added (a list another
    -- migration extended stays whole).
    DO $do$
    DECLARE
        d text;
    BEGIN
        SELECT pg_get_constraintdef(oid) INTO d FROM pg_constraint
            WHERE conname = 'name_actions_action_check' AND conrelid = 'name_actions'::regclass;
        IF (length(d) - length(replace(d, '])', ''))) <> 2 THEN
            RAISE EXCEPTION 'the action list did not have the expected shape';
        END IF;
        ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
        EXECUTE 'ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check '
            || replace(d, '])', ', ''clock-set''::text, ''clock-skip''::text])');
    END
    $do$;

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;

    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = 'dmbot_web_name_action_add';"""
    + _STEPS
    + r"""
        EXECUTE def;
    END
    $do$;
    """
    + READ_DOOR
)

ISOLATED_TABLES: tuple[str, ...] = ("import_clock_offers",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_import_clocks", "dmbot_web_import_clocks(BIGINT, TEXT, TEXT)"),
)
