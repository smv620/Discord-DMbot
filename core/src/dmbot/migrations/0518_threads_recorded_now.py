"""0518_threads_recorded_now

The website's story threads list keeps a session's suggestion only while everyone it quotes is
recorded now, and shows where it was heard (#4526, quests slice 5a, web half).
"""

from __future__ import annotations

# `dmbot_web_threads` (0505) listed every waiting suggestion. A suggestion read from a session
# (0509) quotes people by their id (`speaker_ids`), and the consent rule of every after-session
# proposal is that it is shown only while all its speakers are recorded (Supervisor's ruling B on
# #4522; Discord does it with `ThreadSuggestionStore.waiting(opted_in=…)`). The same repair as 0341
# made for the waiting list: a session's suggestion is listed only when every speaker is recorded
# now by the bot's own rule (`consent._select`): a consent row under the current wording (`terms`)
# and, while an outside company hears the voices, for that company (`outside`). Both arrive as
# arguments because the database cannot know them, so the door has a new signature and the old one
# goes. A suggestion read from lore has no speakers and is listed as before.
#
# The door also says where a suggestion was heard: `session` (true for one read from a session) and
# `heard`, up to three of the lines it points at (`line_starts`), read now from the transcript and
# only from its speakers (all recorded, by the filter above), in the order spoken, as Discord's
# `heard_lines` does. A line deleted since is simply not there. The words are the DM's own
# transcript, shown to a DM of the campaign and to nobody else (the door answers anyone else NULL).
#
# The door is read back from the database as it is and each anchor must be found the exact number of
# times, so a drift stops the migration, as 0341 does. Its pinned search path is set again for the
# new signature; PUBLIC stays revoked. `dmbot_web_thread_lines` (0517) is unchanged: the page joins
# its answer to the suggestions this door lists, so a hidden suggestion's lines never leave.
# No row is changed.
_RECORDED = (
    "s.speaker_ids <@ ARRAY(SELECT n.user_id FROM consent n "
    "WHERE n.guild_id = g AND n.terms_version = terms "
    "AND (outside IS NULL OR n.outside_to = outside))"
)
_WHERE = "WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'waiting'),"
_WHERE_NEW = (
    "WHERE s.guild_id = g AND s.campaign_id = c AND s.status = 'waiting'\n"
    f"                  AND (s.session_id IS NULL OR {_RECORDED})),"
)
_QUOTE = "'quote', s.quote, 'upload', s.upload,"
_QUOTE_NEW = (
    "'quote', s.quote, 'upload', s.upload,\n"
    "                           'session', s.session_id IS NOT NULL,\n"
    "                           'heard', COALESCE((\n"
    "                               SELECT jsonb_agg(h.said ORDER BY h.started_ms, h.id)\n"
    "                               FROM (SELECT l.started_ms, l.id,\n"
    "                                            COALESCE(l.text, l.heard) AS said\n"
    "                                     FROM transcript_lines l\n"
    "                                     WHERE l.guild_id = g AND l.session_id = s.session_id\n"
    "                                       AND l.started_ms = ANY (s.line_starts)\n"
    "                                       AND l.user_id = ANY (s.speaker_ids)\n"
    "                                       AND btrim(COALESCE(l.text, l.heard)) <> ''\n"
    "                                     ORDER BY l.started_ms, l.id LIMIT 3) h),\n"
    "                               '[]'::jsonb),"
)
_HEADER_NEW = (
    "CREATE OR REPLACE FUNCTION dmbot_web_threads(g bigint, c text, terms integer, outside text)"
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


SQL = (
    r"""
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
              AND p.proname = 'dmbot_web_threads';
        def := pg_temp.swap_n(
            def,
            'CREATE OR REPLACE FUNCTION ' || quote_ident(current_schema())
                || '.dmbot_web_threads(g bigint, c text)',
            """
    + _dollar(_HEADER_NEW)
    + r""", 1);
        def := pg_temp.swap_n(def, """
    + _dollar(_QUOTE)
    + ", "
    + _dollar(_QUOTE_NEW)
    + r""", 1);
        def := pg_temp.swap_n(def, """
    + _dollar(_WHERE)
    + ", "
    + _dollar(_WHERE_NEW)
    + r""", 1);
        EXECUTE def;
        EXECUTE format('DROP FUNCTION %I.dmbot_web_threads(BIGINT, TEXT)', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_threads(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_threads(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;
    """
)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_threads", "dmbot_web_threads(BIGINT, TEXT, INT, TEXT)"),
)
