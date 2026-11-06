"""0250_report_answer_door

The DM's answer to one item (#2661).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _REPORT_HERE,
    _REPORT_KEY_C,
    _REPORT_KEY_D,
)

# The DM's answer to one item of a session's after-session report (#2661, slice 4b): a direct
# door, not the bot's queue (Supervisor, 2026-10-27), because an answer is the DM's bookkeeping and
# changes nothing in the story. It records "intended", "not a problem" or "remind me" in
# `report_answers`, or deletes the row for Undo, and answers at once. Inside itself it checks the
# item is in THAT session's report of THIS campaign (the same rule the read door lists by), so a
# key from another session, campaign or server is "gone"; an item that already has a final answer
# is "gone" too (a second press changes nothing), and "remind me" can be answered again. "fix"
# writes nothing: the page opens the Story graph, and recording "fixed" is for when the connection
# changes.
REPORT_ANSWER_DOOR = (
    r"""
    CREATE FUNCTION dmbot_web_report_answer(g BIGINT, c TEXT, at_ BIGINT, k TEXT, act TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        kind_ TEXT;
        had report_answers%ROWTYPE;
        wrote INT;
        now_ BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF k !~ '^[0-9a-f]{32}$'
           OR act NOT IN ('intended', 'not_a_problem', 'remind', 'fix', 'undo') THEN
            RETURN jsonb_build_object('status', 'bad');
        END IF;
        IF EXISTS (SELECT 1 FROM memory_contradictions m
                   LEFT JOIN report_answers a
                     ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_C
    + r"""
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'minor'
                     AND """
    + _REPORT_KEY_C
    + r""" = k
                     AND """
    + _REPORT_HERE
    + r""") THEN
            kind_ := 'contradiction';
        ELSIF EXISTS (SELECT 1 FROM memory_differences m
                      LEFT JOIN report_answers a
                        ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_D
    + r"""
                      WHERE m.guild_id = g AND m.campaign_id = c
                        AND m.sort IN ('disguised', 'misunderstood', 'false')
                        AND """
    + _REPORT_KEY_D
    + r""" = k
                        AND """
    + _REPORT_HERE
    + r""") THEN
            kind_ := 'difference';
        ELSE
            RETURN jsonb_build_object('status', 'gone');
        END IF;
        SELECT * INTO had FROM report_answers
            WHERE guild_id = g AND campaign_id = c AND item_key = k;
        IF act = 'undo' THEN
            IF had.item_key IS NULL THEN
                RETURN jsonb_build_object('status', 'gone');
            END IF;
            DELETE FROM report_answers WHERE guild_id = g AND campaign_id = c AND item_key = k;
            RETURN jsonb_build_object('status', 'ok', 'answer', NULL);
        END IF;
        IF act = 'fix' THEN
            RETURN jsonb_build_object('status', 'ok', 'answer', CASE WHEN had.item_key IS NULL
                THEN NULL ELSE jsonb_build_object('action', had.answer, 'at', had.answered_at) END);
        END IF;
        -- One atomic write: a first answer inserts, a "remind me" may be replaced, and anything
        -- else (a final answer another press or another DM just made) writes nothing. Two
        -- presses at once cannot both win: the second one's conflict finds a final answer.
        INSERT INTO report_answers AS r (guild_id, campaign_id, item_key, item_kind, answer,
                                         answered_by, answered_at, remind_from)
        VALUES (g, c, k, kind_, act, dmbot_current_user(), now_,
                CASE WHEN act = 'remind' THEN at_ END)
        ON CONFLICT (guild_id, campaign_id, item_key) DO UPDATE SET
            item_kind = EXCLUDED.item_kind, answer = EXCLUDED.answer,
            answered_by = EXCLUDED.answered_by, answered_at = EXCLUDED.answered_at,
            remind_from = EXCLUDED.remind_from
        WHERE r.answer = 'remind';
        GET DIAGNOSTICS wrote = ROW_COUNT;
        IF wrote = 0 THEN
            RETURN jsonb_build_object('status', 'gone');  -- already answered: a second press
        END IF;
        RETURN jsonb_build_object('status', 'ok',
                                  'answer', jsonb_build_object('action', act, 'at', now_));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    """
)

SQL = REPORT_ANSWER_DOOR

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_report_answer", "dmbot_web_report_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT)"),
)
