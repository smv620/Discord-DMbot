"""0385_web_rule_clash_decisions

The website lists and changes which of two disagreeing house rules wins (#3697, slice 1).
"""

from __future__ import annotations

# A DM decides, on the website, which of two house rules that disagree wins (#2284); only the DM's
# answer is ever stored (`house_rule_wins`), never a clash. Two new choices join the website's rule
# queue (`web_rule_actions`, 0262), applied by the bot with the same store calls as Discord's card
# (`WinsStore.record` and `forget_pair`):
#   `decide_clash`  arg `W:L`  the campaign's own rule W wins over its own rule L
#   `clear_clash`   arg `A:B`  forget the answer for that pair (either order)
# The queue's `arg` holds 64 characters, which cannot carry a followed set's name, so a pair that
# involves a followed set's rule is listed read-only on the website and changed in Discord. The
# numbers are checked for shape here (two different numbers) and the bot checks again that both
# rules still exist and that the asker is still a DM. Nothing in a row is changed by this
# migration: it widens a CHECK, replaces one door (only 0262 defines it) with the two choices
# added and adds one read door. Replacing a function drops its `SET search_path`, so that and the
# revoke are put back, as 0262 does.
#
# The read door `dmbot_web_rule_decisions(g, c)` answers a DM of the campaign (NULL for anyone else)
# with the campaign's decided pairs and nothing more: no rule's words, no one's name. The page has
# the words from its House rules read.
SQL = r"""
    ALTER TABLE web_rule_actions DROP CONSTRAINT web_rule_actions_action_check;
    ALTER TABLE web_rule_actions ADD CONSTRAINT web_rule_actions_action_check
        CHECK (action IN ('publish', 'stop_sharing', 'look_code', 'follow_here', 'follow_code',
                          'unfollow', 'decide_clash', 'clear_clash'));

    CREATE OR REPLACE FUNCTION dmbot_web_rule_action_add(g BIGINT, c TEXT, act TEXT, a TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        want TEXT := COALESCE(a, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('publish', 'stop_sharing', 'look_code', 'follow_here',
                                      'follow_code', 'unfollow', 'decide_clash',
                                      'clear_clash') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF (act IN ('stop_sharing', 'unfollow') AND want <> '')
           OR (act = 'publish' AND char_length(btrim(want)) NOT BETWEEN 1 AND 60)
           OR (act IN ('look_code', 'follow_code') AND want !~ '^[A-Z2-7]{10}$')
           OR (act = 'follow_here' AND want !~ '^[0-9a-f]{32}$')
           OR (act IN ('decide_clash', 'clear_clash')
               AND (want !~ '^[1-9][0-9]{0,8}:[1-9][0-9]{0,8}$'
                    OR split_part(want, ':', 1) = split_part(want, ':', 2))) THEN
            RETURN jsonb_build_object('status', 'bad_arg');
        END IF;
        IF act = 'publish' THEN
            want := btrim(want);  -- the name is stored as it was checked, and as the store keeps it
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('rule_action:' || g::TEXT || c, 0));
        DELETE FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting, so
        -- it is not stuck behind itself for a day; the bot's own retake does the same. (The page
        -- calls it stalled from five minutes, `progress.STALL_AFTER_S`, so the DM hears of it
        -- before it is put back.) The share codes in `arg` are blanked by the bot as it finishes
        -- a row (#2793); the row itself stays a day.
        UPDATE web_rule_actions r SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state = 'working'
              AND r.updated_at < now_s - 600;
        SELECT r.id INTO have FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.user_id = dmbot_current_user()
              AND r.action = act AND r.arg = want AND r.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF (SELECT count(*) FROM web_rule_actions r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.state IN ('waiting', 'working')) >= 10
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO web_rule_actions (guild_id, campaign_id, id, user_id, action, arg,
                                      created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    CREATE FUNCTION dmbot_web_rule_decisions(g BIGINT, c TEXT) RETURNS JSONB
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
                       'a', jsonb_build_object('set', w.a_set, 'number', w.a_number),
                       'b', jsonb_build_object('set', w.b_set, 'number', w.b_number),
                       'winner', w.winner, 'decidedAt', w.decided_at)
                   ORDER BY w.a_set, w.a_number, w.b_set, w.b_number)
            FROM house_rule_wins w WHERE w.guild_id = g AND w.campaign_id = c), '[]'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_rule_decisions(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_rule_action_add(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_rule_decisions(BIGINT, TEXT) FROM PUBLIC;
"""

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_rule_decisions", "dmbot_web_rule_decisions(BIGINT, TEXT)"),
)
