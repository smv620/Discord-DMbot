"""0409_wizard

The campaign start-up wizard's draft, its checked documents and its finish queue (#3955, part of
#3953; docs/WIZARD_DESIGN.md).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One number for the column check and the door: the most characters of one document's text (the
# lore upload's own limit, migration 0288) and how many documents a draft holds.
TEXT_MAX = 262_144
DOCS_MAX = 20
ANSWERS_MAX = 20_000  # characters of a draft's answers, as JSON

# A draft is not a campaign: it belongs to one DM in one server (`(guild_id, user_id)`), has no
# campaign to hang from, and is gone after 7 days without a press (`expires_at`), on discard, and
# when its finish run has made the campaign. The website's role touches none of these tables
# directly: every call goes through a `dmbot_web_wizard_*` door that checks the signed-in account
# and that the server is in the session's own list. The extracted text of a document is the DM's
# own, never echoed back (a door returns name, counts and status), never in a backup (no campaign to
# back up) and never in a log. The right-to-use confirmations are kept on the draft, one for the
# DM's shared material and one for an uploaded ruleset (Supervisor, #3967), and are carried onto the
# campaign when the bot makes it. Row-level security is the server's, as every server table.
SQL = (
    (
        r"""
    CREATE TABLE wizard_drafts (
        guild_id          BIGINT NOT NULL,
        user_id           BIGINT NOT NULL CHECK (user_id > 0),
        step              INT NOT NULL DEFAULT 1 CHECK (step BETWEEN 1 AND 9),
        answers           JSONB NOT NULL DEFAULT '{}'::jsonb
            CHECK (jsonb_typeof(answers) = 'object'
                   AND char_length(answers::text) <= @ANSWERS_MAX@),
        rights_shared_at  BIGINT NOT NULL DEFAULT 0,
        rights_ruleset_at BIGINT NOT NULL DEFAULT 0,
        created_at        BIGINT NOT NULL,
        updated_at        BIGINT NOT NULL,
        expires_at        BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id)
    );
    CREATE INDEX wizard_drafts_expiry ON wizard_drafts (guild_id, expires_at);

    CREATE TABLE wizard_documents (
        guild_id    BIGINT NOT NULL,
        user_id     BIGINT NOT NULL,
        FOREIGN KEY (guild_id, user_id)
            REFERENCES wizard_drafts (guild_id, user_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        slot        TEXT NOT NULL
            CHECK (slot IN ('lore', 'secrets', 'ruleset', 'house', 'homebrew')),
        name        TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 200),
        source      TEXT NOT NULL CHECK (source IN ('upload', 'link', 'drive')),
        drive_id    TEXT NOT NULL DEFAULT '' CHECK (char_length(drive_id) <= 200),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= @TEXT_MAX@),
        pages       INT NOT NULL DEFAULT 0 CHECK (pages >= 0),
        words       INT NOT NULL DEFAULT 0 CHECK (words >= 0),
        status      TEXT NOT NULL CHECK (status IN ('ok', 'unreadable')),
        reason      TEXT NOT NULL DEFAULT ''
            CHECK (reason IN ('', 'empty', 'scanned', 'locked', 'too_big', 'not_text')),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id, id)
    );

    -- The finish queue the bot answers. No link to the draft: the run deletes the draft at its
    -- last stage and the row stays (a day) so the page can still say "done". `made_campaign` is
    -- set at the first stage so a resumed run never makes a second campaign. One run at a time
    -- for a DM in a server.
    CREATE TABLE wizard_finishes (
        guild_id      BIGINT NOT NULL,
        user_id       BIGINT NOT NULL CHECK (user_id > 0),
        id            TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        state         TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage         TEXT NOT NULL DEFAULT 'waiting' CHECK (stage ~ '^[a-z_]{1,40}$'),
        part          INT NOT NULL DEFAULT 0 CHECK (part >= 0),
        of_parts      INT NOT NULL DEFAULT 0 CHECK (of_parts >= 0),
        made_campaign TEXT NOT NULL DEFAULT ''
            CHECK (made_campaign = '' OR made_campaign ~ '^[0-9a-f]{32}$'),
        error         TEXT NOT NULL DEFAULT '' CHECK (error ~ '^[a-z_-]{0,40}$'),
        created_at    BIGINT NOT NULL,
        claimed_at    BIGINT NOT NULL DEFAULT 0,
        moved_at      BIGINT NOT NULL DEFAULT 0,
        tries         INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id)
    );
    CREATE INDEX wizard_finishes_claim ON wizard_finishes (guild_id, state, created_at);
    CREATE UNIQUE INDEX wizard_finishes_one_running
        ON wizard_finishes (guild_id, user_id) WHERE state IN ('waiting', 'working');
    """
        + _isolate("wizard_drafts")
        + _isolate("wizard_documents")
        + _isolate("wizard_finishes")
        + r"""
    -- Account deletion (#433): a person's drafts go in every server, whatever the server scope.
    -- Two policies let the signed-in person's own rows be deleted across servers, by the function
    -- below and nothing else (the website's role has no grant on these tables).
    -- (A DELETE with a WHERE reads the rows first, so the person's own rows are readable too.)
    CREATE POLICY forget_own ON wizard_drafts FOR SELECT USING (user_id = dmbot_current_user());
    CREATE POLICY forget_own_delete ON wizard_drafts FOR DELETE
        USING (user_id = dmbot_current_user());
    CREATE POLICY forget_own ON wizard_finishes FOR SELECT USING (user_id = dmbot_current_user());
    CREATE POLICY forget_own_delete ON wizard_finishes FOR DELETE
        USING (user_id = dmbot_current_user());
    CREATE FUNCTION dmbot_forget_wizard() RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_user() IS NULL THEN
            RETURN;
        END IF;
        DELETE FROM wizard_drafts WHERE user_id = dmbot_current_user();
        DELETE FROM wizard_finishes WHERE user_id = dmbot_current_user();
    END
    $fn$;

    -- The caller's own live draft's key, or nothing: the checks every door below starts with. The
    -- server must be the one the caller scoped to AND in the session's own list; the account is
    -- the signed-in one. A draft past its expiry is treated as gone.
    CREATE FUNCTION dmbot_wizard_me(g BIGINT) RETURNS BIGINT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR dmbot_current_user() IS NULL THEN
            RETURN NULL;
        END IF;
        RETURN dmbot_current_user();
    END
    $fn$;

    CREATE FUNCTION dmbot_web_wizard_get(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        d wizard_drafts%ROWTYPE;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        -- an expired draft (and its documents, by the cascade) is removed, not only hidden
        DELETE FROM wizard_drafts w
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at <= now_s;
        SELECT * INTO d FROM wizard_drafts w
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'step', d.step, 'answers', d.answers,
            'rightsShared', d.rights_shared_at > 0, 'rightsRuleset', d.rights_ruleset_at > 0,
            'createdAt', d.created_at, 'expiresAt', d.expires_at, 'now', now_s,
            'documents', coalesce((SELECT jsonb_agg(jsonb_build_object(
                'id', x.id, 'slot', x.slot, 'name', x.name, 'source', x.source,
                'pages', x.pages, 'words', x.words, 'status', x.status, 'reason', x.reason)
                ORDER BY x.created_at, x.id)
                FROM wizard_documents x WHERE x.guild_id = g AND x.user_id = me), '[]'::jsonb));
    END
    $fn$;

    -- Make the caller's draft (or return the one they have) with the typed name. Gone drafts are
    -- swept here, so a stale one never blocks a new start.
    CREATE FUNCTION dmbot_web_wizard_start(g BIGINT, nm TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        IF char_length(coalesce(nm, '')) > 100 THEN
            RETURN jsonb_build_object('status', 'bad_name');
        END IF;
        DELETE FROM wizard_drafts w WHERE w.guild_id = g AND w.expires_at <= now_s;
        INSERT INTO wizard_drafts (guild_id, user_id, answers, created_at, updated_at, expires_at)
            VALUES (g, me, CASE WHEN coalesce(nm, '') = '' THEN '{}'::jsonb
                           ELSE jsonb_build_object('name', nm) END,
                    now_s, now_s, now_s + 604800)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET expires_at = now_s + 604800,
                updated_at = now_s;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Merge `patch` into the answers and move to `st`. Only the whitelisted keys, and the whole
    -- answers at most @ANSWERS_MAX@ characters; the value shapes are checked by the Python wrapper
    -- (it knows the invite and ruleset rules), which this door trusts only for those.
    CREATE FUNCTION dmbot_web_wizard_set(g BIGINT, st INT, patch JSONB) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        bad TEXT;
        merged JSONB;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        IF st IS NULL OR st < 1 OR st > 9 OR patch IS NULL OR jsonb_typeof(patch) <> 'object' THEN
            RETURN jsonb_build_object('status', 'bad_patch');
        END IF;
        SELECT k INTO bad FROM jsonb_object_keys(patch) AS k
            WHERE k NOT IN ('name', 'invite', 'source', 'page_text_used', 'characters', 'target',
                            'fallback', 'supplements', 'data_use', 'skipped')
            LIMIT 1;
        IF bad IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'bad_key', 'key', left(bad, 40));
        END IF;
        IF patch ? 'skipped' AND jsonb_typeof(patch->'skipped') <> 'array' THEN
            RETURN jsonb_build_object('status', 'bad_patch');
        END IF;
        SELECT w.answers || patch INTO merged FROM wizard_drafts w
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s FOR UPDATE;
        IF merged IS NULL THEN
            RETURN jsonb_build_object('status', 'no_draft');
        END IF;
        IF char_length(merged::text) > @ANSWERS_MAX@ THEN
            RETURN jsonb_build_object('status', 'too_big');
        END IF;
        UPDATE wizard_drafts w SET answers = merged, step = st, updated_at = now_s,
            expires_at = now_s + 604800
            WHERE w.guild_id = g AND w.user_id = me;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Skip a step: remember that it was skipped (the defaults apply at Finish).
    CREATE FUNCTION dmbot_web_wizard_skip(g BIGINT, st INT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        IF st IS NULL OR st < 1 OR st > 9 THEN
            RETURN jsonb_build_object('status', 'bad_patch');
        END IF;
        UPDATE wizard_drafts w SET
            answers = jsonb_set(w.answers, '{skipped}',
                (SELECT coalesce(jsonb_agg(DISTINCT s ORDER BY s), '[]'::jsonb) FROM
                    jsonb_array_elements(
                        CASE WHEN jsonb_typeof(w.answers->'skipped') = 'array'
                             THEN w.answers->'skipped' ELSE '[]'::jsonb END
                        || to_jsonb(st)) AS s)),
            step = LEAST(st + 1, 9), updated_at = now_s, expires_at = now_s + 604800
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'no_draft');
        END IF;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Add one checked document. The wrapper did the check-only read; this stores what it was given
    -- and keeps the limits (20 a draft, the text size, the slot).
    CREATE FUNCTION dmbot_web_wizard_doc_add(
        g BIGINT, sl TEXT, nm TEXT, src TEXT, drv TEXT, txt TEXT, pg INT, wd INT, st TEXT, rsn TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        did TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM wizard_drafts w
                       WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s) THEN
            RETURN jsonb_build_object('status', 'no_draft');
        END IF;
        IF sl IS NULL OR sl NOT IN ('lore', 'secrets', 'ruleset', 'house', 'homebrew')
           OR src IS NULL OR src NOT IN ('upload', 'link', 'drive')
           OR st IS NULL OR st NOT IN ('ok', 'unreadable')
           OR coalesce(rsn, '') NOT IN ('', 'empty', 'scanned', 'locked', 'too_big', 'not_text')
           OR coalesce(pg, 0) < 0 OR coalesce(wd, 0) < 0
           OR char_length(coalesce(nm, '')) NOT BETWEEN 1 AND 200
           OR char_length(coalesce(drv, '')) > 200 THEN
            RETURN jsonb_build_object('status', 'bad_document');
        END IF;
        IF char_length(coalesce(txt, '')) > @TEXT_MAX@ THEN
            RETURN jsonb_build_object('status', 'too_big');
        END IF;
        IF (SELECT count(*) FROM wizard_documents x WHERE x.guild_id = g AND x.user_id = me)
           >= @DOCS_MAX@ THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        INSERT INTO wizard_documents (guild_id, user_id, id, slot, name, source, drive_id, text,
                                      pages, words, status, reason, created_at)
            VALUES (g, me, did, sl, nm, src, coalesce(drv, ''), coalesce(txt, ''),
                    coalesce(pg, 0), coalesce(wd, 0), st, coalesce(rsn, ''), now_s);
        UPDATE wizard_drafts w SET updated_at = now_s, expires_at = now_s + 604800
            WHERE w.guild_id = g AND w.user_id = me;
        RETURN jsonb_build_object('status', 'ok', 'id', did);
    END
    $fn$;

    CREATE FUNCTION dmbot_web_wizard_doc_remove(g BIGINT, did TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        DELETE FROM wizard_documents x WHERE x.guild_id = g AND x.user_id = me AND x.id = did;
        RETURN jsonb_build_object('status', CASE WHEN FOUND THEN 'ok' ELSE 'not_found' END);
    END
    $fn$;

    -- The right-to-use confirmation, once for the shared material (`shared`) and once for an
    -- uploaded ruleset (`ruleset`); the account is the "who", the time is recorded.
    CREATE FUNCTION dmbot_web_wizard_rights(g BIGINT, kind TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        IF kind IS NULL OR kind NOT IN ('shared', 'ruleset') THEN
            RETURN jsonb_build_object('status', 'bad_patch');
        END IF;
        UPDATE wizard_drafts w SET
            rights_shared_at = CASE WHEN kind = 'shared' THEN now_s ELSE w.rights_shared_at END,
            rights_ruleset_at = CASE WHEN kind = 'ruleset' THEN now_s ELSE w.rights_ruleset_at END,
            updated_at = now_s, expires_at = now_s + 604800
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'no_draft');
        END IF;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    CREATE FUNCTION dmbot_web_wizard_discard(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        DELETE FROM wizard_drafts w WHERE w.guild_id = g AND w.user_id = me;
        RETURN jsonb_build_object('status', 'ok');
    END
    $fn$;

    -- Queue the finish run. The draft needs a name (or Skip taken, with the name typed) and the
    -- right-to-use confirmation for every slot that needs it; a second press while one runs gets
    -- that run's id; at most 3 presses a minute. The plan's room is checked by the bot at its first
    -- stage; the page-side early check (`campaign_cap.room`) comes with the routes, in the next PR.
    -- A run taken as lost here may still be working: the bot's stage writes must be conditional on
    -- state = 'working', and its first stage must look for a campaign already made from this
    -- draft (`made_campaign`), so a lost-and-retried run never makes a second one. The
    -- right-to-use times are carried, with this account as the "who", onto the campaign.
    CREATE FUNCTION dmbot_web_wizard_finish(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        d wizard_drafts%ROWTYPE;
        running TEXT;
        fid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('wizard_finish:' || g::TEXT || ':' || me::TEXT, 0));
        DELETE FROM wizard_drafts w
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at <= now_s;
        DELETE FROM wizard_finishes f
            WHERE f.guild_id = g AND f.user_id = me AND f.created_at < now_s - 86400;
        UPDATE wizard_finishes f SET state = 'failed', error = 'lost'
            WHERE f.guild_id = g AND f.user_id = me AND f.state IN ('waiting', 'working')
              AND greatest(f.created_at, f.moved_at) < now_s - 1800;
        SELECT * INTO d FROM wizard_drafts w
            WHERE w.guild_id = g AND w.user_id = me AND w.expires_at > now_s;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'no_draft');
        END IF;
        SELECT f.id INTO running FROM wizard_finishes f
            WHERE f.guild_id = g AND f.user_id = me AND f.state IN ('waiting', 'working');
        IF running IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', running);
        END IF;
        IF coalesce(d.answers->>'name', '') = '' THEN
            RETURN jsonb_build_object('status', 'needs_name');
        END IF;
        IF d.rights_shared_at = 0 AND EXISTS (
               SELECT 1 FROM wizard_documents x WHERE x.guild_id = g AND x.user_id = me
                 AND x.slot IN ('lore', 'secrets', 'house', 'homebrew')) THEN
            RETURN jsonb_build_object('status', 'needs_rights');
        END IF;
        IF d.rights_ruleset_at = 0 AND EXISTS (
               SELECT 1 FROM wizard_documents x WHERE x.guild_id = g AND x.user_id = me
                 AND x.slot = 'ruleset') THEN
            RETURN jsonb_build_object('status', 'needs_rights');
        END IF;
        IF (SELECT count(*) FROM wizard_finishes f
            WHERE f.guild_id = g AND f.user_id = me AND f.created_at >= now_s - 60) >= 3 THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO wizard_finishes (guild_id, user_id, id, created_at, moved_at)
            VALUES (g, me, fid, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', fid);
    END
    $fn$;

    -- Where the caller's latest finish run stands: stage, part of parts, error word, the campaign
    -- once it exists, and the times the progress card shows. Counts and words only.
    CREATE FUNCTION dmbot_web_wizard_finish_state(g BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_wizard_me(g);
        r wizard_finishes%ROWTYPE;
    BEGIN
        IF me IS NULL THEN
            RETURN NULL;
        END IF;
        SELECT * INTO r FROM wizard_finishes f
            WHERE f.guild_id = g AND f.user_id = me ORDER BY f.created_at DESC, f.id LIMIT 1;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'id', r.id, 'state', r.state, 'stage', r.stage, 'part', r.part, 'ofParts', r.of_parts,
            'campaign', r.made_campaign, 'error', r.error, 'createdAt', r.created_at,
            'movedAt', r.moved_at, 'now', floor(extract(epoch FROM clock_timestamp()))::BIGINT);
    END
    $fn$;
    """
    )
    .replace("@TEXT_MAX@", str(TEXT_MAX))
    .replace("@DOCS_MAX@", str(DOCS_MAX))
    .replace("@ANSWERS_MAX@", str(ANSWERS_MAX))
)

_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_wizard_me", "dmbot_wizard_me(BIGINT)"),
    ("dmbot_forget_wizard", "dmbot_forget_wizard()"),
    ("dmbot_web_wizard_get", "dmbot_web_wizard_get(BIGINT)"),
    ("dmbot_web_wizard_start", "dmbot_web_wizard_start(BIGINT, TEXT)"),
    ("dmbot_web_wizard_set", "dmbot_web_wizard_set(BIGINT, INT, JSONB)"),
    ("dmbot_web_wizard_skip", "dmbot_web_wizard_skip(BIGINT, INT)"),
    (
        "dmbot_web_wizard_doc_add",
        "dmbot_web_wizard_doc_add(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, INT, INT, TEXT, TEXT)",
    ),
    ("dmbot_web_wizard_doc_remove", "dmbot_web_wizard_doc_remove(BIGINT, TEXT)"),
    ("dmbot_web_wizard_rights", "dmbot_web_wizard_rights(BIGINT, TEXT)"),
    ("dmbot_web_wizard_discard", "dmbot_web_wizard_discard(BIGINT)"),
    ("dmbot_web_wizard_finish", "dmbot_web_wizard_finish(BIGINT)"),
    ("dmbot_web_wizard_finish_state", "dmbot_web_wizard_finish_state(BIGINT)"),
)

# Each function runs with the schema it was made in on its search path (as the other doors), and is
# callable by nobody but the website's role (granted through WEB_ROLE_FUNCTIONS).
SQL += "DO $do$\nBEGIN\n"
for _name, _sig in _FUNCTIONS:
    SQL += (
        f"    EXECUTE format('ALTER FUNCTION {_sig} SET search_path = pg_catalog, %I, pg_temp',"
        " current_schema());\n"
    )
SQL += "END\n$do$;\n"
for _name, _sig in _FUNCTIONS:
    SQL += f"REVOKE ALL ON FUNCTION {_sig} FROM PUBLIC;\n"

ISOLATED_TABLES: tuple[str, ...] = ("wizard_drafts", "wizard_documents", "wizard_finishes")

# `dmbot_wizard_me` is a helper the doors call: the website's role needs no grant on it.
# `dmbot_forget_wizard` is account deletion's call, made on the website like the other forgets.
WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = tuple(
    pair for pair in _FUNCTIONS if pair[0] != "dmbot_wizard_me"
)
