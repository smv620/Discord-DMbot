"""0437_met_list_doors

The website's "Met but not named" list (#4084, the web half of #4045): one read door for the DM,
and the three queued answers (`met-name`, `met-join`, `met-remove`) the bot already applies
(`web_actions.MET_ACTIONS`).
"""

from __future__ import annotations

# The read door lists the rows of `memory_met` for a DM of the campaign, newest session first, at
# most 250 (`MetStore.LIST_MAX`), and leaves out any row whose speakers are not all recorded in this
# server now: the same re-check the bot does before it reads (`consent.still_recorded`: a consent
# row under the current wording, or an import confirmation not taken back with no "no" on record; a
# speaker without Discord, a negative number, is never taken as stopped). It sends the words, the
# count and the session's date, and never `line_ms` or `speaker_ids`. The caller passes the current
# wording's number (`terms`), as the waiting list's door does.
#
# The queue door is read back from the database as it is (many migrations have changed it) and each
# anchor must be found the exact number of times, so a drift stops the migration instead of widening
# the wrong rule (the 0434 way). `entity_id` of these actions is the list's row (`memory_met.id`),
# so the row must exist; `met-join` also takes a confirmed entry as its target, checked like the
# other joins; `met-name` carries the name as its text; none of them carries a flag.
_SWAPS = (
    # the allowed actions
    (
        "'told-add', 'told-remove', 'clock-set', 'clock-skip')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        "'told-add', 'told-remove', 'clock-set', 'clock-skip',\n"
        "                'met-name', 'met-join', 'met-remove')\n        THEN\n"
        "            RETURN jsonb_build_object('status', 'bad_action');",
        1,
    ),
    # a name needs its text
    (
        "'name', 'offer-clear')\n               AND clean = '')",
        "'name', 'offer-clear', 'met-name')\n               AND clean = '')",
        1,
    ),
    # a join and a remove carry no text
    (
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no', 'clock-set', 'clock-skip')\n"
        "               AND clean <> '')",
        "'name-join', 'name-undo', 'spelling-yes', 'spelling-no', 'clock-set', 'clock-skip',\n"
        "                       'met-join', 'met-remove')\n               AND clean <> '')",
        1,
    ),
    # a join needs a target, and nothing else may carry one
    (
        "'told-add', 'told-remove', 'clock-set', 'clock-skip')\n               AND (tgt IS NULL",
        "'told-add', 'told-remove', 'clock-set', 'clock-skip', 'met-join')\n"
        "               AND (tgt IS NULL",
        1,
    ),
    (
        "'told-add', 'told-remove', 'clock-set', 'clock-skip')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'told-add', 'told-remove', 'clock-set', 'clock-skip', 'met-join')\n"
        "               AND COALESCE(tgt, '') <> '')",
        1,
    ),
    # no flag
    (
        "'told-add', 'told-remove', 'clock-set', 'clock-skip')\n"
        "               AND COALESCE(fl, false))\n           OR (act IN ('link-words',",
        "'told-add', 'told-remove', 'clock-set', 'clock-skip',\n"
        "                       'met-name', 'met-join', 'met-remove')\n"
        "               AND COALESCE(fl, false))\n           OR (act IN ('link-words',",
        1,
    ),
    # a join goes to a confirmed entry, like the other joins
    (
        "IF act IN ('merge', 'link-add', 'name-join') AND (tgt = subject",
        "IF act IN ('merge', 'link-add', 'name-join', 'met-join') AND (tgt = subject",
        1,
    ),
    # the list's row must exist in this campaign (the bot checks again when it applies it)
    (
        "        ELSIF is_link AND act <> 'link-add' THEN",
        "        ELSIF act LIKE 'met-%' THEN\n"
        "            IF NOT EXISTS (SELECT 1 FROM memory_met m WHERE m.guild_id = g\n"
        "                           AND m.campaign_id = c AND m.id = ent) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            END IF;\n"
        "        ELSIF is_link AND act <> 'link-add' THEN",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _steps(swaps: tuple[tuple[str, str, int], ...]) -> str:
    return "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )


SQL = (
    r"""
    -- The allowed actions, read back from the table as it is and three more added at the end, so an
    -- action another migration added stays in the list whatever the order the migrations merge in.
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
            || replace(d, '])', ', ''met-name''::text, ''met-join''::text, ''met-remove''::text])');
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
    + _steps(_SWAPS)
    + r"""
        EXECUTE def;
    END
    $do$;

    -- The DM's "Met but not named" list (NULL for anyone who is not a DM of the campaign).
    CREATE FUNCTION dmbot_web_met_list(g BIGINT, c TEXT, terms INTEGER) RETURNS JSONB
    LANGUAGE plpgsql STABLE SECURITY DEFINER AS $fn$
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
                       'id', t.id, 'what', t.what, 'count', t.count,
                       'sessionStartedAt', t.session_started_at)
                       ORDER BY t.session_started_at DESC NULLS LAST, t.created_at, t.id)
            FROM (
                SELECT m.* FROM memory_met m
                WHERE m.guild_id = g AND m.campaign_id = c
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(m.speaker_ids) u
                      WHERE u > 0 AND NOT (
                          EXISTS (SELECT 1 FROM consent n
                                  WHERE n.guild_id = g AND n.user_id = u
                                    AND n.terms_version = terms)
                          OR (EXISTS (SELECT 1 FROM import_consents i
                                      WHERE i.guild_id = g AND i.campaign_id = c
                                        AND i.user_id = u AND i.removed_at IS NULL)
                              AND NOT EXISTS (SELECT 1 FROM consent_refusals r
                                              WHERE r.guild_id = g AND r.user_id = u))))
                ORDER BY m.session_started_at DESC NULLS LAST, m.created_at, m.id
                LIMIT 250) t), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_met_list(BIGINT, TEXT, INTEGER)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_met_list(BIGINT, TEXT, INTEGER) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_met_list", "dmbot_web_met_list(BIGINT, TEXT, INTEGER)"),
)
