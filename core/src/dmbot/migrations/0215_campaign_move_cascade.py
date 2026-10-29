"""0215_campaign_move_cascade

A campaign can change server (#2532).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _CASCADE_ALL_KEYS,
)

CAMPAIGN_MOVE_CASCADE = (
    _CASCADE_ALL_KEYS
    + """

    -- The bot's lease on a move that is under way, and the door that asks for a move, made again
    -- (0214 is the same as it was when it first shipped): it now also checks that the caller is
    -- scoped to the campaign's server, goes back to that scope when the target has no DMbot,
    -- and wakes the bot. A new column and a function: no rows change.
    ALTER TABLE campaign_handover_offers ADD COLUMN move_claimed_at BIGINT;
    CREATE OR REPLACE FUNCTION dmbot_handover_request_move(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_target BIGINT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ready BOOLEAN;
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_target IS NULL OR p_target = g
           OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN false;  -- (the caller connects scoped to the campaign's server, like every door)
        END IF;
        IF NOT (p_target = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN false;
        END IF;
        -- Whether DMbot is installed in the target is a fact of the TARGET server, whose rows are
        -- visible only when scoped to it: so the door looks there for this one read and comes
        -- straight back. It reads nothing else there.
        PERFORM set_config('dmbot.guild_id', p_target::text, true);
        SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = p_target AND left_at IS NULL)
            INTO ready;
        IF NOT ready THEN
            PERFORM set_config('dmbot.guild_id', g::text, true);  -- back where the caller was
            RETURN false;
        END IF;
        PERFORM set_config('dmbot.guild_id', g::text, true);
        UPDATE campaign_handover_offers f
            SET to_user_id = me, to_name = left(coalesce(p_name, ''), 80),
                move_to_guild_id = p_target
            FROM campaigns c
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me) AND f.from_user_id <> me
              AND f.move_to_guild_id IS NULL
              AND c.guild_id = f.guild_id AND c.id = f.campaign_id
              AND c.owner_user_id = f.from_user_id;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed = 1 THEN  -- wakes the bot, which does the move (a sweep catches a missed one)
            PERFORM pg_notify('dmbot_handover_move', g::text || ':' || o::text);
        END IF;
        RETURN changed = 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- The one write that may take a campaign out of the server in scope: the move sets both
    -- settings in its own transaction (campaigns/moves.py), and the policy then lets that one
    -- campaign be seen and written in the server it goes to. (It is for all commands because an
    -- update's new row must also be readable, which a policy for updates alone does not give.)
    -- Policies add up, so with the settings unset every other write stays as it was.
    -- Only for the role the bot connects as (the one that ran this migration), as `move_all` is:
    -- the website's role never gets it.
    DO $do$
    BEGIN
        EXECUTE format(
            'CREATE POLICY campaign_move ON campaigns TO %I'
            ' USING ((guild_id = dmbot_current_guild()'
            '         OR guild_id = NULLIF(current_setting(''dmbot.moving_to'', true),'
            ' '''')::BIGINT)'
            '        AND id = NULLIF(current_setting(''dmbot.moving_campaign'', true), ''''))'
            ' WITH CHECK (guild_id = NULLIF(current_setting(''dmbot.moving_to'', true),'
            ' '''')::BIGINT'
            '             AND id = NULLIF(current_setting(''dmbot.moving_campaign'', true), ''''))',
            current_user);
    END
    $do$;

    -- Whether the campaign an emailed link is for shares a rule set: such a campaign cannot move to
    -- another server yet (#2551), and the page says so before anyone accepts or buys a plan. Only
    -- the link's own secret opens the question.
    CREATE FUNCTION dmbot_handover_shares_rules(g BIGINT, o BIGINT, p_hash TEXT) RETURNS BOOLEAN
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT g IS NOT NULL AND o IS NOT NULL AND p_hash ~ '^[0-9a-f]{64}$'
            AND current_setting('dmbot.guild_id', true) IS NOT DISTINCT FROM g::text
            AND EXISTS (
                SELECT 1 FROM campaign_handover_offers f
                JOIN rule_sets s ON s.guild_id = f.guild_id AND s.campaign_id = f.campaign_id
                WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
                  AND f.status = 'open' AND s.status = 'shared')
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- Three more facts about a hand-over that moves: when the move was asked for (an offer whose
    -- move never ran is opened again after an hour), and, when a move did not happen, who had
    -- accepted and the plain reason, which only that person is shown (the owner is told only that
    -- it did not happen, because the reason can be about the other person's plan).
    ALTER TABLE campaign_handover_offers
        ADD COLUMN move_asked_at BIGINT,
        ADD COLUMN move_failed_for BIGINT,
        ADD COLUMN move_failed_reason TEXT;
    CREATE FUNCTION dmbot_note_move_asked() RETURNS trigger
        LANGUAGE plpgsql
        AS $fn$
    BEGIN
        IF NEW.move_to_guild_id IS NOT NULL AND OLD.move_to_guild_id IS NULL THEN
            NEW.move_asked_at := dmbot_now();
            NEW.move_failed_for := NULL;
            NEW.move_failed_reason := NULL;
        END IF;
        RETURN NEW;
    END
    $fn$;
    CREATE TRIGGER campaign_handover_offers_move_asked
        BEFORE UPDATE OF move_to_guild_id ON campaign_handover_offers
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_move_asked();

    -- The plain reason a move did not happen, for the person who had accepted and nobody else
    -- (the owner is told only that it did not happen). Shown on that person's page.
    CREATE FUNCTION dmbot_handover_move_failed(g BIGINT, o BIGINT, p_hash TEXT) RETURNS TEXT
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT f.move_failed_reason FROM campaign_handover_offers f
        WHERE dmbot_current_user() IS NOT NULL AND f.guild_id = g AND f.id = o AND f.via_email
          AND f.token_hash = p_hash AND f.status = 'open'
          AND f.move_failed_for = dmbot_current_user()
          AND current_setting('dmbot.guild_id', true) IS NOT DISTINCT FROM g::text
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_move_failed(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_move_failed(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- The server it arrives in is one that holds campaigns now (the retention job's routing list).
    CREATE TRIGGER campaign_guilds_note_move
        AFTER UPDATE OF guild_id ON campaigns
        FOR EACH ROW EXECUTE FUNCTION dmbot_note_campaign_guild();
"""
)

SQL = CAMPAIGN_MOVE_CASCADE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_handover_shares_rules", "dmbot_handover_shares_rules(BIGINT, BIGINT, TEXT)"),  # #2532
    ("dmbot_handover_move_failed", "dmbot_handover_move_failed(BIGINT, BIGINT, TEXT)"),  # #2532
)
