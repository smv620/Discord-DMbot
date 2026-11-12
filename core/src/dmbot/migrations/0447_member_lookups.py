"""0447_member_lookups

The website's person pickers can take a typed Discord ID (#4225, point 5): the page asks the bot who
it is, and shows the name before the id is used. A row is the question and the bot's answer.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row per question. `target` is the id the DM typed: a Discord id the person has already seen
# in Discord, kept only while the answer is wanted (the bot sweeps rows an hour old, the add door
# a day). The bot looks the member up in THIS server (`fetch_member`, no privileged intent) and
# writes `shown` ("Eric (eric_92)") and `outcome`: 'found', or 'not_here' (not in the server, or
# a bot). The row holds the name for the asker's page and nothing else; it is never written into
# a campaign. The door checks the caller may ask (a DM here, or someone with a live wizard) and
# asks no more than 20 a minute; the id must be a 17 to 19 digit number (a Discord snowflake fits
# a BIGINT). `claimed_at` is the bot's own stamp in milliseconds, moved as it works; a question
# waiting or working 10 minutes without moving is taken as lost, as the door reads it too.
# `agreed` is read when the page asks, never stored: whether this person has agreed to be
# recorded in this server.
SQL = (
    r"""
    CREATE TABLE member_lookups (
        guild_id   BIGINT NOT NULL,
        id         TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        user_id    BIGINT NOT NULL CHECK (user_id > 0),
        target     BIGINT NOT NULL CHECK (target > 0),
        state      TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        outcome    TEXT NOT NULL DEFAULT '' CHECK (outcome IN ('', 'found', 'not_here')),
        shown      TEXT NOT NULL DEFAULT '' CHECK (char_length(shown) <= 220),
        created_at BIGINT NOT NULL,
        claimed_at BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX member_lookups_claim ON member_lookups (guild_id, state, created_at);
    """
    + _isolate("member_lookups")
    + r"""
    -- Who may ask: a DM of a campaign in this server, or someone with a live start-up wizard here
    -- (the person pickers are DM tools, and the answer says whether the person has agreed to be
    -- recorded, which is not for every member of the server to read). Internal: the doors call it.
    CREATE FUNCTION dmbot_may_look_up_members(g BIGINT) RETURNS BOOLEAN
    LANGUAGE sql STABLE SECURITY DEFINER AS $fn$
        SELECT dmbot_current_user() IS NOT NULL AND (
            EXISTS (SELECT 1 FROM campaign_dms d
                    WHERE d.guild_id = g AND d.user_id = dmbot_current_user())
            OR EXISTS (SELECT 1 FROM wizard_drafts w
                       WHERE w.guild_id = g AND w.user_id = dmbot_current_user()
                         AND w.expires_at > floor(extract(epoch FROM clock_timestamp()))::BIGINT))
    $fn$;
    -- Ask who a Discord id is (NULL unless the caller may, see above).
    -- 'bad_id' for anything but a 17 to 19 digit number; 'too_soon' past 20 questions a minute.
    CREATE FUNCTION dmbot_web_member_lookup_add(g BIGINT, who BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT dmbot_may_look_up_members(g) THEN
            RETURN NULL;
        END IF;
        IF who IS NULL OR who < 10000000000000000 THEN
            RETURN jsonb_build_object('status', 'bad_id');
        END IF;
        DELETE FROM member_lookups m WHERE m.guild_id = g AND m.created_at < now_s - 86400;
        IF (SELECT count(*) FROM member_lookups m
            WHERE m.guild_id = g AND m.user_id = dmbot_current_user()
              AND m.created_at >= now_s - 60) >= 20 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO member_lookups (guild_id, id, user_id, target, created_at)
            VALUES (g, rid, dmbot_current_user(), who, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- Where one question stands, for the person who asked it; NULL when there is none such. The
    -- name is given only once the bot found the member. `agreed`: has this person agreed to be
    -- recorded here (the pickers mark the ones who have not).
    CREATE FUNCTION dmbot_web_member_lookup_read(g BIGINT, lid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r member_lookups%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        st TEXT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT dmbot_may_look_up_members(g) THEN
            RETURN NULL;
        END IF;
        SELECT m.* INTO r FROM member_lookups m
            WHERE m.guild_id = g AND m.id = lid AND m.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        st := r.state;
        IF st IN ('waiting', 'working')
           AND GREATEST(r.created_at, r.claimed_at / 1000) < now_s - 600 THEN
            st := 'failed';  -- nobody answered: the page is told, not left spinning
        END IF;
        RETURN jsonb_build_object(
            'state', st, 'outcome', r.outcome,
            'name', CASE WHEN r.outcome = 'found' THEN r.shown ELSE '' END,
            'agreed', CASE WHEN r.outcome = 'found' THEN
                EXISTS (SELECT 1 FROM consent c WHERE c.guild_id = g AND c.user_id = r.target)
                ELSE NULL END);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_may_look_up_members(BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_member_lookup_add(BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_member_lookup_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    -- Account deletion (#4141 pattern, 0431 and 0440): a person's own questions go, and so does any
    -- row that asked about them (the typed id is theirs too). The policies are for the function's
    -- owner only and open only while it runs.
    DO $do$
    BEGIN
        EXECUTE format('CREATE POLICY forget_lookups_read ON member_lookups FOR SELECT TO %I
            USING (dmbot_owner_sync() = ''forget_lookups''
                   AND (user_id = dmbot_current_user() OR target = dmbot_current_user()))',
            current_user);
        EXECUTE format('CREATE POLICY forget_lookups_delete ON member_lookups FOR DELETE TO %I
            USING (dmbot_owner_sync() = ''forget_lookups''
                   AND (user_id = dmbot_current_user() OR target = dmbot_current_user()))',
            current_user);
    END
    $do$;
    CREATE FUNCTION dmbot_forget_lookups() RETURNS VOID
        LANGUAGE plpgsql SECURITY DEFINER
        AS $fn$
    DECLARE
        before TEXT := COALESCE(current_setting('dmbot.owner_sync', true), '');
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        PERFORM set_config('dmbot.owner_sync', 'forget_lookups', true);
        DELETE FROM member_lookups
            WHERE user_id = dmbot_current_user() OR target = dmbot_current_user();
        -- (an error rolls the setting back, so only success restores it)
        PERFORM set_config('dmbot.owner_sync', before, true);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_forget_lookups() SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_forget_lookups() FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_may_look_up_members(BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_member_lookup_add(BIGINT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_member_lookup_read(BIGINT, TEXT) FROM PUBLIC;
"""
)

# (table, the column that holds the person's Discord ID): the catalog test reads this. A row is also
# removed when `target` is the person (see the function above).
DELETED: tuple[tuple[str, str], ...] = (("member_lookups", "user_id"),)

ISOLATED_TABLES: tuple[str, ...] = ("member_lookups",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_member_lookup_add", "dmbot_web_member_lookup_add(BIGINT, BIGINT)"),
    ("dmbot_web_member_lookup_read", "dmbot_web_member_lookup_read(BIGINT, TEXT)"),
    # Account deletion's call, made on the website like the other forgets.
    ("dmbot_forget_lookups", "dmbot_forget_lookups()"),
)
