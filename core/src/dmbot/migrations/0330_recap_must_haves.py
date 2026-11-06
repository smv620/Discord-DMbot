"""0330_recap_must_haves

The must-haves every recap is written to (#3328; docs/RECAP_STANDARD.md): a campaign's own list,
saved from the website through the recap style queue.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The limits (recaps/must_haves.py: MAX_LINES, LINE_CHARS; the website page mirrors them). A web
# door cannot import them, so the bot checks the same numbers again and a test keeps these in step.
MAX_LINES = 40
LINE_CHARS = 300
# The whole list as the queue holds it: JSON text, so up to 40 lines of 300 characters, each
# possibly escaped to six characters, plus the quotes and commas. (The website sends it with
# `ensure_ascii=False`, so a real list is far under this; the door refuses anything over it with
# `too_long` instead of letting the table's check fail.)
ARG_CHARS = 80_000

RECAP_MUST_HAVES = (
    r"""
    -- A campaign's own must-have list for its recaps (#3328). With no rows the campaign uses
    -- DMbot's defaults, which live in code (recaps/must_have_defaults/), never here. A line is
    -- short plain text; at most @MAX_LINES@ of at most @LINE_CHARS@ characters (the store checks
    -- both). Per campaign, isolated, deleted with the campaign, and in its backup.
    CREATE TABLE recap_must_haves (
        {SCOPE}
        -- Never reused, so a stale page cannot point at a newer line.
        id          BIGINT GENERATED ALWAYS AS IDENTITY,
        position    INTEGER NOT NULL CHECK (position >= 0),
        text        TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND @LINE_CHARS@),
        created_by  BIGINT NOT NULL CHECK (created_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    """.replace(
        "{SCOPE}",
        # (ON UPDATE CASCADE like the tables a move rewrites, #2550: `_memory_scope` has none)
        """
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,""",
    )
    + _isolate("recap_must_haves")
    + r"""
    -- The style queue (0290) also carries the whole must-have list: `save_musts` (`arg` is the
    -- list as a JSON array of texts) and `musts_defaults` (no `arg`). The bot blanks `arg` as it
    -- finishes a row, as for a pasted example.
    ALTER TABLE web_recap_style_actions DROP CONSTRAINT web_recap_style_actions_action_check;
    ALTER TABLE web_recap_style_actions ADD CONSTRAINT web_recap_style_actions_action_check
        CHECK (action IN ('add_example', 'remove_example', 'use_defaults', 'add_note',
                          'remove_note', 'save_musts', 'musts_defaults'));
    ALTER TABLE web_recap_style_actions DROP CONSTRAINT web_recap_style_actions_arg_check;
    ALTER TABLE web_recap_style_actions ADD CONSTRAINT web_recap_style_actions_arg_check
        CHECK (char_length(arg) <= @ARG_CHARS@);

    -- dmbot_web_recap_style_add (0290), now also queueing the two must-have actions. The door
    -- checks the shape of a saved list: a JSON array of at most @MAX_LINES@ strings, each of 1 to
    -- @LINE_CHARS@ characters once trimmed. 'bad_arg' (not a list, or an empty line), 'too_long',
    -- 'too_many'.
    CREATE OR REPLACE FUNCTION dmbot_web_recap_style_add(g BIGINT, c TEXT, act TEXT, a TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        want TEXT := COALESCE(a, '');
        parsed JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('add_example', 'remove_example', 'use_defaults',
                                      'add_note', 'remove_note', 'save_musts',
                                      'musts_defaults') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF act IN ('add_example', 'add_note') THEN
            want := btrim(want, E' \t\r\n\f\v');
        END IF;
        IF act = 'save_musts' THEN
            IF char_length(want) > @ARG_CHARS@ THEN
                RETURN jsonb_build_object('status', 'too_long');
            END IF;
            BEGIN
                parsed := want::JSONB;
            EXCEPTION WHEN OTHERS THEN
                RETURN jsonb_build_object('status', 'bad_arg');
            END;
            IF jsonb_typeof(parsed) IS DISTINCT FROM 'array'
               OR EXISTS (SELECT 1 FROM jsonb_array_elements(parsed) e
                          WHERE jsonb_typeof(e) IS DISTINCT FROM 'string'
                             OR btrim(e #>> '{}', E' \t\r\n\f\v') = '') THEN
                RETURN jsonb_build_object('status', 'bad_arg');
            END IF;
            IF jsonb_array_length(parsed) > @MAX_LINES@ THEN
                RETURN jsonb_build_object('status', 'too_many');
            END IF;
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(parsed) e
                       WHERE char_length(btrim(e #>> '{}', E' \t\r\n\f\v')) > @LINE_CHARS@) THEN
                RETURN jsonb_build_object('status', 'too_long');
            END IF;
        END IF;
        IF ((act = 'use_defaults' OR act = 'musts_defaults') AND want <> '')
           OR (act IN ('remove_example', 'remove_note') AND want !~ '^[0-9]{1,18}$')
           OR (act = 'add_example' AND (want = '' OR char_length(want) > @EXAMPLE_CHARS@
               OR array_length(regexp_split_to_array(want, '[ \t\r\n\f\v]+'), 1) > @EXAMPLE_WORDS@))
           OR (act = 'add_note' AND char_length(want) NOT BETWEEN 1 AND @NOTE_CHARS@) THEN
            RETURN jsonb_build_object('status', 'bad_arg');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('recap_style:' || g::TEXT || ':' || c, 0));
        UPDATE web_recap_style_actions r SET arg = ''
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('done', 'failed')
              AND r.arg <> '';
        DELETE FROM web_recap_style_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        UPDATE web_recap_style_actions r SET state = 'waiting', claimed_at = 0, updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        UPDATE web_recap_style_actions r SET state = 'failed', error = 'not_taken', arg = '',
                                             updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'waiting'
              AND r.updated_at < now_s - 1800;
        IF act = 'add_example' AND (SELECT count(*) FROM recap_style_examples e
                                    WHERE e.guild_id = g AND e.campaign_id = c) >= 3 THEN
            RETURN jsonb_build_object('status', 'full');
        END IF;
        IF (SELECT count(*) FROM web_recap_style_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_recap_style_actions (guild_id, campaign_id, id, user_id, action, arg,
                                             created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- dmbot_web_recap_style (0290), now also returning the DM's own must-have lines
    -- (`mustLines`, by position; empty means DMbot's defaults, which the website's API adds from
    -- code, with their groups).
    CREATE OR REPLACE FUNCTION dmbot_web_recap_style(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        ex JSONB;
        nt JSONB;
        ml JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', e.id::TEXT, 'text', e.text)
                                  ORDER BY e.position, e.id), '[]'::jsonb) INTO ex
            FROM recap_style_examples e WHERE e.guild_id = g AND e.campaign_id = c;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', n.id::TEXT, 'text', n.text)
                                  ORDER BY n.position, n.id), '[]'::jsonb) INTO nt
            FROM recap_style_notes n WHERE n.guild_id = g AND n.campaign_id = c;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id', m.id::TEXT, 'text', m.text)
                                  ORDER BY m.position, m.id), '[]'::jsonb) INTO ml
            FROM recap_must_haves m WHERE m.guild_id = g AND m.campaign_id = c;
        RETURN jsonb_build_object('examples', ex, 'notes', nt,
                                  'defaults', jsonb_array_length(ex) = 0, 'mustLines', ml);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_style_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_recap_style(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
"""
)

SQL = (
    RECAP_MUST_HAVES.replace("@MAX_LINES@", str(MAX_LINES))
    .replace("@LINE_CHARS@", str(LINE_CHARS))
    .replace("@ARG_CHARS@", str(ARG_CHARS))
    .replace("@EXAMPLE_CHARS@", "8000")
    .replace("@EXAMPLE_WORDS@", "600")
    .replace("@NOTE_CHARS@", "300")
)

ISOLATED_TABLES: tuple[str, ...] = ("recap_must_haves",)
