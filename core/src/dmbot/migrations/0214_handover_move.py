"""0214_handover_move

A hand-over to another server is asked for (#2532).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    HANDOVER_CLAIM,
)

HANDOVER_MOVE = """
    -- A campaign handed over by email to a person who wants it in another server (#2532, slice C;
    -- docs/PLAN.md, "Hand-over by email", point 7). The website cannot copy a campaign between
    -- servers (it must never read or write another server's tables), so it only records the wish
    -- here: `move_to_guild_id`. DMbot's bot then moves the campaign: one update of its row, which
    -- migration 0215 lets every table follow. (`moved_campaign_id` was for an earlier design that
    -- copied the campaign; nothing writes it.) While a move is asked for, the offer stays 'open'
    -- but cannot be answered, taken back or run out. Two new columns on an existing table: no
    -- rows change.
    ALTER TABLE campaign_handover_offers
        ADD COLUMN move_to_guild_id BIGINT
            CHECK (move_to_guild_id IS NULL OR move_to_guild_id > 0),
        ADD COLUMN moved_campaign_id TEXT;

    -- Claims the link for this person and asks for the move, in one step (#2532). Same rules as
    -- dmbot_handover_open with a claim, and the target must be one of the session's own servers
    -- with DMbot installed. NULL, whatever the reason, the same as any ended link.
    CREATE FUNCTION dmbot_handover_request_move(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_target BIGINT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        ready BOOLEAN;
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_target IS NULL OR p_target = g
           OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$' THEN
            RETURN false;
        END IF;
        IF NOT (p_target = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN false;
        END IF;
        PERFORM set_config('dmbot.guild_id', p_target::text, true);
        SELECT EXISTS (SELECT 1 FROM installs WHERE guild_id = p_target AND left_at IS NULL)
            INTO ready;
        IF NOT ready THEN
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
    REVOKE ALL ON FUNCTION dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)
        FROM PUBLIC;
"""

# A pending move makes the doors of 0213 look away (#2532, slice C): the claimant's look says the
# link has ended, a release leaves the claim alone, and nobody can decline underneath the move.
# Built from 0213's own text, so the two cannot drift: the same doors, the same grants and pinned
# search_path, with one more condition each.
_MOVE_BLIND = (
    HANDOVER_CLAIM.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    .replace(
        "AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()",
        "AND f.status = 'open' AND f.move_to_guild_id IS NULL"
        " AND f.created_at + 604800 > dmbot_now()",
    )
    .replace(
        "AND status = 'open' AND to_user_id = me;",
        "AND status = 'open' AND move_to_guild_id IS NULL AND to_user_id = me;",
    )
)

assert _MOVE_BLIND.count("move_to_guild_id IS NULL") == 3, "0214 must change all three doors"

HANDOVER_MOVE += _MOVE_BLIND

SQL = HANDOVER_MOVE

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "dmbot_handover_request_move",
        "dmbot_handover_request_move(BIGINT, BIGINT, TEXT, TEXT, BIGINT)",
    ),  # #2532, slice C (granted now that the bot's move exists)
)
