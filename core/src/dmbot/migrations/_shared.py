"""What more than one migration builds its SQL from (#2127): the helper functions, the pieces
that several migrations share, and the website role's name. Moved unchanged from
schema.py by scripts/split_migrations.py."""

from __future__ import annotations


def _isolate(table: str) -> str:
    """Row-level security: only the current server's rows are visible or writable."""
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY guild_isolation ON {table}
        USING (guild_id = dmbot_current_guild())
        WITH CHECK (guild_id = dmbot_current_guild());
    """


# Shared columns and links for campaign-memory tables: scoped to one server AND one
# campaign, and deleted with the campaign.
def _memory_scope() -> str:
    return """
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE,
    """


def _entity_link(column: str, *, cascade: bool = True) -> str:
    """A link to an entity in the SAME server and campaign (a composite key, so a row
    can never point into another campaign)."""
    action = " ON DELETE CASCADE" if cascade else ""
    return f"""
        FOREIGN KEY (guild_id, campaign_id, {column})
            REFERENCES memory_entities (guild_id, campaign_id, id){action}"""


def _isolate_user(table: str) -> str:
    """Row-level security for a person's own rows (website accounts, #435): only the
    signed-in user's rows are visible or writable. Set by Database.user()."""
    return f"""
    ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;
    ALTER TABLE {table} FORCE ROW LEVEL SECURITY;
    CREATE POLICY user_isolation ON {table}
        USING (user_id = dmbot_current_user())
        WITH CHECK (user_id = dmbot_current_user());
    """


def _setting(name: str, setting: str, kind: str) -> str:
    """A function reading one per-transaction setting, or NULL when it isn't set."""
    return f"""
    CREATE FUNCTION {name}() RETURNS {kind}
        LANGUAGE sql STABLE
        AS $fn$ SELECT NULLIF(current_setting('{setting}', true), '')::{kind} $fn$;
    """


RECAP_APPROVAL = """
    -- The DM approves a recap's text before any audio is made, and picks what to make (#2083).
    -- approved_hash is a fingerprint of the title and summary the DM approved (sha256 of
    -- "title\\nsummary", the audio's own `text_hash`), so an edit after approval makes it stale
    -- with no one having to clear it: an approval counts only while the hash matches the text.
    -- audio_choice: voice, music, both, or text (no audio). Defaulted columns change no rows, so
    -- no row-level-security policy is needed.
    ALTER TABLE recap_jobs ADD COLUMN approved_by BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN approved_at BIGINT NOT NULL DEFAULT 0;
    ALTER TABLE recap_jobs ADD COLUMN approved_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE recap_jobs ADD COLUMN audio_choice TEXT NOT NULL DEFAULT ''
        CHECK (audio_choice IN ('', 'voice', 'music', 'both', 'text'));

    -- Approving from the website. The website's role may only change the columns its grant names,
    -- so approving goes through this door: only a DM of the campaign, only a recap that is ready
    -- and still the DM's to post. It records who and when (the database's own clock) and the hash
    -- of the text as it is now, and, for any choice but text, queues the audio the way the bot
    -- does. The page checks the cues first; the secret names and the plan are checked again by the
    -- audio step before a paid call, as for every audio.
    CREATE FUNCTION dmbot_recap_approve(
        g BIGINT, c TEXT, at_ BIGINT, choice TEXT, expected TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF choice NOT IN ('voice', 'music', 'both', 'text') THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' OR job.status <> 'draft' THEN
            RETURN 'not_allowed';
        END IF;
        -- The page checked the cues of one text; only that text is approved. An edit committed
        -- since (a different hash) approves nothing.
        IF expected <> encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                              'hex') THEN
            RETURN 'not_allowed';
        END IF;
        -- While the audio is queued or being made nothing is approved over it, text only
        -- included: the spend so far would be lost and the caps dodged. Audio made from older
        -- text (the DM has edited since) may be made again from the new text.
        IF job.audio = 'queued' THEN
            RETURN 'not_allowed';
        END IF;
        IF choice <> 'text' AND NOT (
            job.audio IN ('off', 'failed')
            OR (job.audio = 'ready' AND job.audio_text_hash <> encode(
                sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex'))
        ) THEN
            RETURN 'not_allowed';
        END IF;
        UPDATE recap_jobs SET
            approved_by = dmbot_current_user(), approved_at = now_s,
            approved_hash = encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')),
                                   'hex'),
            audio_choice = choice,
            audio = CASE WHEN choice = 'text' THEN job.audio ELSE 'queued' END,
            audio_tries = CASE WHEN choice = 'text' THEN job.audio_tries ELSE 0 END,
            audio_next_at = CASE WHEN choice = 'text' THEN job.audio_next_at ELSE 0 END,
            audio_error = CASE WHEN choice = 'text' THEN job.audio_error ELSE '' END
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'approved';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;

    -- The names the website must check a recap's text against before it approves (#2083): the
    -- campaign's secret names (a secret alias, or any name of an entry limited to some of the
    -- players) and its confirmed names that are not secret and not someone at the table, so
    -- "Arcane Brotherhood" inside the confirmed "Arcane Brotherhood Hall" is read the way the bot
    -- reads it, and who is recorded in the server under the current terms and the outside engine
    -- in use, as the bot's consent check reads it (the website's role sees only a person's own
    -- consent).
    -- Only a DM of the campaign, in a server on the session's list. The API uses them to
    -- check one text and never returns them: the website's role has no grant on the memory tables.
    CREATE FUNCTION dmbot_recap_check_names(g BIGINT, c TEXT, terms INT, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'secret', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND (a.secret OR e.audience_limited)), '[]'::jsonb),
            'allowed', COALESCE((
                SELECT jsonb_agg(DISTINCT a.text)
                FROM memory_aliases a JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND NOT e.audience_limited
                  AND a.status = 'confirmed' AND e.status = 'confirmed'
                  AND e.type <> 'person'), '[]'::jsonb),
            'recorded', COALESCE((
                SELECT jsonb_agg(c2.user_id) FROM consent c2
                WHERE c2.guild_id = g AND c2.terms_version = terms
                  AND (outside IS NULL OR c2.outside_to = outside)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_check_names(BIGINT, TEXT, INT, TEXT) FROM PUBLIC;

    -- "Make the audio again" needs a current approval (#2083): the audio is only ever made for a
    -- text the DM approved with a choice that makes audio, and it is made as they chose. The same
    -- door as before (#1418) with that one condition more.
    CREATE OR REPLACE FUNCTION dmbot_recap_audio_again(g BIGINT, c TEXT, at_ BIGINT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_allowed';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND OR job.state <> 'ready' OR job.status NOT IN ('draft', 'posting')
           OR job.audio NOT IN ('ready', 'failed') THEN
            RETURN 'not_allowed';
        END IF;
        IF job.audio_choice NOT IN ('voice', 'music', 'both')
           OR job.approved_hash <> encode(
               sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex') THEN
            RETURN 'needs_approval';
        END IF;
        IF job.audio_asked_at > now_s - 600 THEN
            RETURN 'too_soon';
        END IF;
        UPDATE recap_jobs SET audio = 'queued', audio_tries = 0, audio_next_at = 0,
                              audio_error = '', audio_asked_at = now_s
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        RETURN 'queued';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_audio_again(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
"""

# The columns the website may fill when it creates a hand-over offer (#728): who, where and the
# two names. Left out on purpose, so they take their defaults: status and decided_at (an offer
# starts open), and delivered_at, claimed_at, message_id and end_told_at, which only the bot
# sets (a writer that leaves delivered_at empty gets its offer sent by the bot). Not granted
# yet: no website route creates offers. The grant, when it comes, is
# `INSERT (<these>)` on campaign_handover_offers; the policy `web_new_offer` above backs it.
OFFER_INSERT_COLUMNS: tuple[str, ...] = (
    "guild_id",
    "campaign_id",
    "from_user_id",
    "to_user_id",
    "created_at",
    "from_name",
    "to_name",
    # By email (#2532): the link's hash, the mark that says so, and delivered_at, set at once
    # because the bot has no Discord message to send for it (the policy allows that only here).
    "via_email",
    "token_hash",
    "delivered_at",
)

WEB_NAMES = """
    -- The campaign's names on the website, with the narrator's "said as" spelling (#1451, on
    -- #1419). The website's role has NO grant on the memory tables (secret names live there), so
    -- both the read and the write go through these two doors. Each answers only a DM of the
    -- campaign, in a server on the session's list, with that server set for the transaction (the
    -- caller does the same for every server table), and never a secret name or a person.
    --
    -- dmbot_web_names: the confirmed, public names of things and places (not people at the table,
    -- not a sound-alike "misheard" note), at most 200, as JSON; NULL when the caller is no DM of
    -- the campaign, so the page says "not found" exactly as for a missing campaign.
    CREATE FUNCTION dmbot_web_names(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
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
                       'id', n.id, 'text', n.text, 'kind', n.kind, 'saidAs', n.said_as))
            FROM (
                SELECT a.id, a.text, a.kind, a.said_as
                FROM memory_aliases a
                JOIN memory_entities e
                  ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id
                 AND e.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c
                  AND NOT a.secret AND a.status = 'confirmed'
                  AND e.status = 'confirmed' AND e.type <> 'person'
                  AND a.kind <> 'misheard' AND a.used_by IS NULL
                ORDER BY lower(e.name), lower(a.text), a.id
                LIMIT 200
            ) n), '[]'::jsonb);
    END
    $fn$;

    -- dmbot_web_set_said_as: set or clear (NULL or empty) one name's spelling. The same rules as
    -- MemoryStore.set_said_as: only a DM, up to 80 characters, no control characters, never on a
    -- secret name. It writes through the change log exactly as the bot does (one numbered batch,
    -- the row's before and after, the campaign's memory version, the change notice the bot's
    -- name lists follow), so Undo on the Discord name card still takes it back. Answers
    -- 'ok', 'not_allowed' (not this campaign's DM), 'not_found' (no such name, or a secret one) or
    -- 'too_long'. The 80-character limit and the control-character check are the database's own
    -- floor; the API (web/names.py) applies the bot's fuller printable-characters rule first.
    -- Any listed name may be given a spelling, not only an entry's main name: the narrator reads
    -- the spelling from every name (#1419), and the page lists exactly what the read door lists.
    CREATE FUNCTION dmbot_web_set_said_as(g BIGINT, c TEXT, alias_id TEXT, spelling TEXT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        clean TEXT := NULLIF(
            btrim(regexp_replace(COALESCE(spelling, ''), '[[:space:]]+', ' ', 'g')), '');
        alias_row memory_aliases%ROWTYPE;
        v BIGINT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_allowed';
        END IF;
        IF clean IS NOT NULL AND (char_length(clean) > 80 OR clean ~ '[[:cntrl:]]') THEN
            RETURN 'too_long';
        END IF;
        -- Changes to one campaign's memory happen one at a time (the bot takes the same lock).
        SELECT memory_version INTO v FROM campaigns
            WHERE guild_id = g AND id = c FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        SELECT a.* INTO alias_row FROM memory_aliases a
            JOIN memory_entities e
              ON e.guild_id = a.guild_id AND e.campaign_id = a.campaign_id AND e.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = alias_id
              AND NOT a.secret AND a.status = 'confirmed' AND e.status = 'confirmed'
              AND e.type <> 'person' AND a.kind <> 'misheard' AND a.used_by IS NULL;
        -- A secret name is "not found", the same answer as an id that does not exist, so the
        -- caller cannot learn that a secret name with that id exists.
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        IF alias_row.said_as IS NOT DISTINCT FROM clean THEN
            RETURN 'ok';  -- nothing to change, nothing to log (as the bot does)
        END IF;
        UPDATE memory_aliases SET said_as = clean
            WHERE guild_id = g AND campaign_id = c AND id = alias_id;
        v := v + 1;
        INSERT INTO memory_changes (guild_id, campaign_id, version, batch, table_name, row_id, op,
                                    before, after, source, undoes, made_at)
            SELECT g, c, v, v, 'memory_aliases', alias_id, 'update',
                   to_jsonb(alias_row) - 'guild_id' - 'campaign_id',
                   to_jsonb(a) - 'guild_id' - 'campaign_id', 'dm', NULL, now_s
            FROM memory_aliases a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = alias_id;
        UPDATE campaigns SET memory_version = v WHERE guild_id = g AND id = c;
        PERFORM pg_notify('dmbot_memory', c || ':' || v || ':1');
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_names(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_names(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_set_said_as(BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
"""

WEB_TRANSCRIPT_PAGES = """
    -- Transcripts on the website, part two (#1512): a size cap and the character labels.
    --
    -- dmbot_web_transcript_lines now answers one page of at most 5,000 lines, in the order the
    -- speech started (started_ms, then the row's own order, the same order as
    -- `TranscriptStore.lines`), starting after the cursor (after_ms, after_id; both NULL for the
    -- first page). The answer is {"lines": [...], "next": {"ms": ..., "id": ...} or null}: `next`
    -- is the cursor for the page after, null on the last. The same checks as before: the caller is
    -- in the server (set for the transaction, on the session's list) and the session is this
    -- campaign's, else NULL.
    DROP FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT);
    CREATE FUNCTION dmbot_web_transcript_lines(
        g BIGINT, c TEXT, sid TEXT, after_ms BIGINT, after_id BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        page_size CONSTANT INT := 5000;
        found JSONB;
        last_ms BIGINT;
        last_id BIGINT;
        more BOOLEAN;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM transcript_sessions t
                          WHERE t.guild_id = g AND t.campaign_id = c AND t.id = sid) THEN
            RETURN NULL;
        END IF;
        -- One row more than a page, to know whether there is a next one.
        WITH page AS (
            SELECT l.*, row_number() OVER (ORDER BY l.started_ms, l.id) AS rn
            FROM (SELECT * FROM transcript_lines t
                  WHERE t.guild_id = g AND t.session_id = sid
                    AND (after_ms IS NULL OR (t.started_ms, t.id) > (after_ms, after_id))
                  ORDER BY t.started_ms, t.id
                  LIMIT page_size + 1) l
        )
        SELECT jsonb_agg(jsonb_build_object(
                   'startedMs', p.started_ms, 'userId', p.user_id::TEXT,
                   'heard', p.heard, 'text', COALESCE(p.text, p.heard),
                   'durationMs', p.duration_ms, 'topic', p.topic,
                   'entity', COALESCE(p.entity, ''), 'sidebar', COALESCE(p.sidebar, ''),
                   'ref', p.sidebar_ref, 'replyTo', p.sidebar_reply_to,
                   'via', p.sidebar_via, 'stt', p.sidebar_stt, 'model', p.sidebar_model,
                   'prompt', p.sidebar_prompt, 'sources', p.sidebar_sources,
                   'typed', p.typed) ORDER BY p.rn) FILTER (WHERE p.rn <= page_size),
               (array_agg(p.started_ms) FILTER (WHERE p.rn = page_size))[1],
               (array_agg(p.id) FILTER (WHERE p.rn = page_size))[1],
               COALESCE(bool_or(p.rn > page_size), FALSE)
          INTO found, last_ms, last_id, more
          FROM page p;
        RETURN jsonb_build_object(
            'lines', COALESCE(found, '[]'::jsonb),
            'next', CASE WHEN more THEN jsonb_build_object('ms', last_ms, 'id', last_id)
                         ELSE NULL END);
    END
    $fn$;

    -- dmbot_web_transcript_characters: who plays which character in the campaign, as a JSON object
    -- of Discord user id (text) -> the character's name, for the labels `{Cerric}` the Discord
    -- file carries. The export's rule (`cleaner.characters`, then the plays-links of
    -- `MemoryStore.table_people`), in SQL, and a little stricter so a name is never shown that
    -- the campaign's memory has not confirmed:
    --   1. a confirmed player character with a player, whose own name is a confirmed alias that is
    --      not secret; a player with more than one gets the newest (ties: the lowest id);
    --   2. for a person at the table with none of those: the last confirmed, non-secret `plays`
    --      link to a confirmed character whose own name is such an alias.
    -- A DM of the campaign never gets one (a DM may voice an NPC, and the file must not tell
    -- which). Any member of the server may ask, as for the transcripts; NULL when the caller is
    -- not in the server (or no campaign).
    CREATE FUNCTION dmbot_web_transcript_characters(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        RETURN COALESCE((
            SELECT jsonb_object_agg(x.user_id::TEXT, x.name)
            FROM (
                WITH named AS (
                    -- every confirmed, non-secret character with a name of its own
                    SELECT e.id, e.created_at, e.role, e.played_by, a.text, a.id AS alias_id
                    FROM memory_entities e
                    JOIN memory_aliases a
                      ON a.guild_id = e.guild_id AND a.campaign_id = e.campaign_id
                     AND a.entity_id = e.id
                    WHERE e.guild_id = g AND e.campaign_id = c
                      AND e.type <> 'person' AND e.status = 'confirmed'
                      AND a.status = 'confirmed' AND NOT a.secret
                      AND a.key = (SELECT n.key FROM memory_aliases n
                                   WHERE n.guild_id = g AND n.campaign_id = c
                                     AND n.entity_id = e.id AND n.text = e.name
                                     AND n.status <> 'rejected'
                                   ORDER BY n.id LIMIT 1)
                ), by_role AS (
                    SELECT DISTINCT ON (played_by) played_by AS user_id, text AS name
                    FROM named
                    WHERE role = 'player_character' AND played_by IS NOT NULL
                    ORDER BY played_by, created_at DESC, id, alias_id
                ), by_plays AS (
                    SELECT DISTINCT ON (p.played_by) p.played_by AS user_id, nm.text AS name
                    FROM memory_entities p
                    JOIN memory_relations r
                      ON r.guild_id = p.guild_id AND r.campaign_id = p.campaign_id
                     AND r.subject_id = p.id
                    JOIN (SELECT DISTINCT ON (id) id, text FROM named ORDER BY id, alias_id) nm
                      ON nm.id = r.object_id
                    WHERE p.guild_id = g AND p.campaign_id = c AND p.type = 'person'
                      AND p.status = 'confirmed' AND p.played_by IS NOT NULL
                      AND r.predicate = 'plays' AND r.status = 'confirmed' AND NOT r.secret
                      AND NOT EXISTS (SELECT 1 FROM by_role b WHERE b.user_id = p.played_by)
                    ORDER BY p.played_by, r.created_at DESC, r.id DESC
                )
                SELECT user_id, name FROM by_role
                UNION ALL
                SELECT user_id, name FROM by_plays
            ) x
            WHERE NOT EXISTS (SELECT 1 FROM campaign_dms d
                              WHERE d.guild_id = g AND d.campaign_id = c
                                AND d.user_id = x.user_id)), '{}'::jsonb);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_transcript_characters(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_lines(BIGINT, TEXT, TEXT, BIGINT, BIGINT)
        FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_transcript_characters(BIGINT, TEXT) FROM PUBLIC;
"""

NAME_ANSWERS = (
    f"""
    -- The names a recap kept that DMbot did not know (#2027) and that the DM is asked to check, now
    -- answerable on the recap page (#2113). `recap_name_checks` only says which waiting
    -- suggestions belong to which recap (ids, no words): written when the bot queues them. A
    -- name stops being listed when it is no longer a suggestion, whoever answered it, here or in
    -- Discord's 📝 Check new names. Per campaign, isolated, gone with the name and the campaign,
    -- left out of backups (a pointer, not content).
    CREATE TABLE recap_name_checks (
        {_memory_scope()}
        session_at  BIGINT NOT NULL,
        entity_id   TEXT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, session_at, entity_id),
        {_entity_link("entity_id")}
    );

    -- An answer the DM gave on the page, waiting for the bot to apply it through the same
    -- MemoryStore paths as Discord's (so the change log, Undo and the lists all agree). The
    -- website has no right on the memory tables, so it only queues the answer; the bot applies
    -- it and marks the row. `text` is the new spelling (at most 80 letters) or the DM's note (at
    -- most 300); a row lives an hour at most and is never logged. Gone with its name and its
    -- campaign, left out of backups, isolated per server.
    CREATE TABLE name_answers (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        entity_id   TEXT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        action      TEXT NOT NULL
            CHECK (action IN ('confirm', 'same', 'respell', 'not_a_name', 'explain')),
        kind        TEXT NOT NULL DEFAULT '' CHECK (char_length(kind) <= 40),
        target_id   TEXT NOT NULL DEFAULT '' CHECK (char_length(target_id) <= 32),
        text        TEXT NOT NULL DEFAULT '' CHECK (char_length(text) <= 300),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, id),
        {_entity_link("entity_id")}
    );
    -- One answer in flight for a name: a second is "busy" until the first is done.
    CREATE UNIQUE INDEX name_answers_in_flight ON name_answers (guild_id, campaign_id, entity_id)
        WHERE state IN ('waiting', 'working');
    CREATE INDEX name_answers_open ON name_answers (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("recap_name_checks")
    + _isolate("name_answers")
    + r"""
    -- The recap's names to check, for a DM of the campaign: each waiting suggestion the recap
    -- kept (id, name, the note kept with it, whether an answer is on its way or failed), and the
    -- known names a suggestion can be said to be (confirmed, public, not a person at the table).
    -- Never a secret name: a suggestion with a secret spelling is left out, and so is a known
    -- name with any secret spelling (the same rule as the queue door and the bot). NULL for anyone
    -- else.
    CREATE FUNCTION dmbot_recap_names_read(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object(
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'id', e.id, 'name', e.name, 'note', e.description,
                           'answering', EXISTS (
                               SELECT 1 FROM name_answers a
                               WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                                 AND a.state IN ('waiting', 'working')),
                           'error', COALESCE((
                               SELECT a.error FROM name_answers a
                               WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                                 AND a.state = 'failed'
                               ORDER BY a.created_at DESC LIMIT 1), ''))
                       ORDER BY lower(e.name), e.id)
                FROM recap_name_checks k
                JOIN memory_entities e
                  ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                 AND e.id = k.entity_id
                WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
                  AND e.status = 'proposed'
                  AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                  WHERE s.guild_id = g AND s.campaign_id = c
                                    AND s.entity_id = e.id AND s.secret)), '[]'::jsonb),
            'known', COALESCE((
                SELECT jsonb_agg(jsonb_build_object('id', n.id, 'name', n.name, 'kind', n.type)
                                 ORDER BY lower(n.name), n.id)
                FROM (
                    SELECT e.id, e.name, e.type
                    FROM memory_entities e
                    WHERE e.guild_id = g AND e.campaign_id = c
                      AND e.status = 'confirmed' AND e.type <> 'person'
                      AND EXISTS (SELECT 1 FROM memory_aliases a
                                  WHERE a.guild_id = g AND a.campaign_id = c
                                    AND a.entity_id = e.id AND NOT a.secret
                                    AND a.status = 'confirmed'
                                    AND lower(a.text) = lower(e.name))
                      AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                      WHERE s.guild_id = g AND s.campaign_id = c
                                        AND s.entity_id = e.id AND s.secret)
                    ORDER BY lower(e.name), e.id
                    LIMIT 200
                ) n), '[]'::jsonb));
    END
    $fn$;

    -- Queue the DM's answer to one of the recap's names to check. The door checks what it can
    -- see: the caller is a DM of the campaign, the name is one of this recap's waiting
    -- suggestions, the words are within their limits, and nothing is in flight for it; the bot
    -- checks the rest (the kind, the known name) when it applies the answer. Answers 'ok' with
    -- the answer's id, or 'bad_action', 'bad_text', 'bad_target', 'not_found', 'busy' or
    -- 'too_soon'.
    CREATE FUNCTION dmbot_name_answer_add(
        g BIGINT, c TEXT, at_ BIGINT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN ('confirm', 'same', 'respell', 'not_a_name', 'explain') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]'
           OR (act = 'respell' AND char_length(clean) NOT BETWEEN 1 AND 80)
           OR (act = 'explain' AND char_length(clean) NOT BETWEEN 1 AND 300)
           OR (act IN ('confirm', 'same', 'not_a_name') AND clean <> '')
           OR (act = 'confirm' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'confirm' AND COALESCE(kd, '') <> '')
           OR (act = 'same' AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act <> 'same' AND COALESCE(tgt, '') <> '') THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        -- A merge target must be one the list offers: confirmed, not a person at the table, and
        -- with no secret spelling. (The bot checks again when it applies the answer.)
        IF act = 'same' AND NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person'
                 AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                 WHERE s.guild_id = g AND s.campaign_id = c
                                   AND s.entity_id = t.id AND s.secret)) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_answer:' || g::TEXT || c, 0));
        DELETE FROM name_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF NOT EXISTS (SELECT 1 FROM recap_name_checks k
                       JOIN memory_entities e
                         ON e.guild_id = k.guild_id AND e.campaign_id = k.campaign_id
                        AND e.id = k.entity_id
                       WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_
                         AND k.entity_id = ent AND e.status = 'proposed'
                         AND NOT EXISTS (SELECT 1 FROM memory_aliases s
                                         WHERE s.guild_id = g AND s.campaign_id = c
                                           AND s.entity_id = e.id AND s.secret)) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF EXISTS (SELECT 1 FROM name_answers a
                   WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent
                     AND a.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM name_answers a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        DELETE FROM name_answers a  -- a failed answer is replaced by the new one
            WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = ent
              AND a.state = 'failed';
        INSERT INTO name_answers (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, created_at)
            VALUES (g, c, rid, ent, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_name_answer_add('
            'BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_name_answer_add(
        BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    """
)

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
ALL_NAMES_PAGE = r"""
    -- The website's All names page (#2294): one page of a campaign's confirmed names, searched and
    -- filtered, with the count of each kind. Answers a DM of the campaign (everything) and a server
    -- manager who is not one (the same names without what only a DM sees: the entries the players
    -- don't know yet, a clue only some know, the story threads and deeds, and any name whose own
    -- spelling is secret, and no secret spellings). NULL for anyone else, so the page says "not
    -- found" exactly as for a missing campaign. The website's role has no grant on the memory
    -- tables; this door is its only way to read them.
    CREATE FUNCTION dmbot_web_all_names(
        g BIGINT, c TEXT, q TEXT, k TEXT, newest BOOLEAN, pg INT, size INT, only_id TEXT
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        cname TEXT;
        needle TEXT := NULLIF(lower(btrim(COALESCE(q, ''))), '');
        kind_ TEXT := NULLIF(k, '');
        result JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF NOT is_dm AND NOT (g = ANY ((SELECT dmbot_web_managed_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT name INTO cname FROM campaigns WHERE guild_id = g AND id = c;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        WITH base AS (
            SELECT e.id, e.name, e.created_at,
                   CASE WHEN e.type = 'character' THEN
                            CASE e.role WHEN 'player_character' THEN 'player_character'
                                        WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                        ELSE 'character' END
                        ELSE e.type END AS kind,
                   EXISTS (SELECT 1 FROM memory_aliases a
                           WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                             AND a.status <> 'rejected' AND a.secret
                             AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                               = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                   ) AS secret,
                   (e.audience_limited OR e.hidden_from_players) AS hidden
            FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c
              AND e.status = 'confirmed' AND e.type <> 'person'
              AND (is_dm OR (NOT e.audience_limited AND NOT e.hidden_from_players
                             AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                'promise', 'deed')))
        ), vis AS (
            SELECT * FROM base WHERE is_dm OR NOT secret
        ), hit AS (
            SELECT v.* FROM vis v
            WHERE (kind_ IS NULL OR v.kind = kind_)
              AND (only_id IS NULL OR v.id = only_id)
              AND (needle IS NULL
                   OR position(needle IN lower(v.name)) > 0
                   OR EXISTS (SELECT 1 FROM memory_aliases a
                              WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = v.id
                                AND a.status = 'confirmed' AND (is_dm OR NOT a.secret)
                                AND position(needle IN lower(a.text)) > 0))
        ), pageful AS (
            SELECT h.* FROM hit h
            ORDER BY CASE WHEN newest THEN NULL ELSE lower(h.name) END,
                     CASE WHEN newest THEN h.created_at END DESC, h.id
            LIMIT GREATEST(size, 1) OFFSET GREATEST(pg - 1, 0)::BIGINT * GREATEST(size, 1)
        )
        SELECT jsonb_build_object(
            'campaign', cname,
            'canEdit', is_dm,
            'total', (SELECT count(*) FROM hit),
            'kinds', COALESCE((SELECT jsonb_object_agg(kind, n)
                               FROM (SELECT kind, count(*) AS n FROM vis GROUP BY kind) t),
                              '{}'::jsonb),
            'names', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', p.id, 'name', p.name, 'kind', p.kind, 'secret', p.secret,
                    'hidden', p.hidden,
                    'others', COALESCE((
                        SELECT jsonb_agg(a.text ORDER BY lower(a.text), a.id)
                        FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = p.id
                          AND a.status = 'confirmed' AND NOT a.secret
                          AND lower(a.text) <> lower(p.name)), '[]'::jsonb),
                    'secretOthers', CASE WHEN is_dm THEN COALESCE((
                        SELECT jsonb_agg(a.text ORDER BY lower(a.text), a.id)
                        FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = p.id
                          AND a.status = 'confirmed' AND a.secret
                          AND lower(a.text) <> lower(p.name)), '[]'::jsonb)
                        ELSE '[]'::jsonb END,
                    'npcId', CASE WHEN p.kind = 'npc' AND EXISTS (
                        SELECT 1 FROM npc_roster r
                        WHERE r.guild_id = g AND r.campaign_id = c AND r.entry_id = p.id)
                        THEN p.id END)
                    ORDER BY CASE WHEN newest THEN NULL ELSE lower(p.name) END,
                             CASE WHEN newest THEN p.created_at END DESC, p.id)
                FROM pageful p), '[]'::jsonb))
        INTO result;
        RETURN result;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_all_names(BIGINT, TEXT, TEXT, TEXT, BOOLEAN, INT, INT, TEXT)
        FROM PUBLIC;
"""

# The All names door again (#2376, migration 0174): the check for an entry whose own spelling is
# also a secret name ran once per entry over all of its names, which took seconds on a big campaign
# before the database had statistics (right after a restore or an import). It is now found from the
# secret names alone and joined once. The result is the same. 0170 is already applied everywhere,
# so this redefines the door; a redefined function loses its pinned settings, so they are set again.
_SECRET_EACH = """                   EXISTS (SELECT 1 FROM memory_aliases a
                           WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                             AND a.status <> 'rejected' AND a.secret
                             AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                               = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')
                   ) AS secret,"""

_SECRET_ONCE = """                   (e.id IN (SELECT s.entity_id FROM secret_own s)) AS secret,"""

_SECRET_CTE = """        WITH secret_own AS (
            SELECT DISTINCT a.entity_id
            FROM memory_aliases a
            JOIN memory_entities en ON en.guild_id = a.guild_id AND en.campaign_id = a.campaign_id
                                   AND en.id = a.entity_id
            WHERE a.guild_id = g AND a.campaign_id = c AND a.secret AND a.status <> 'rejected'
              AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
        ), base AS ("""

assert _SECRET_EACH in ALL_NAMES_PAGE and ALL_NAMES_PAGE.count("        WITH base AS (") == 1

ALL_NAMES_SPEED = (
    ALL_NAMES_PAGE.replace(
        "CREATE FUNCTION dmbot_web_all_names(", "CREATE OR REPLACE FUNCTION dmbot_web_all_names(", 1
    )
    .replace(_SECRET_EACH, _SECRET_ONCE, 1)
    .replace("        WITH base AS (", _SECRET_CTE, 1)
)

# Which suggested names were made before pronouns were kept out of them (#2358). The default
# marks every existing row (a constant default is stored as metadata: no UPDATE runs, so no
# row-level-security policy is opened) and is then changed, so every row written from now on is
# false with no insert path changed. The recap page's door tells the website which are old.
_NAMES_READ = NAME_ANSWERS[
    NAME_ANSWERS.index("    CREATE FUNCTION dmbot_recap_names_read") : NAME_ANSWERS.index(
        "    -- Queue the DM's answer"
    )
]

NAME_GATE = (
    """
    ALTER TABLE memory_entities
        ADD COLUMN made_before_name_gate BOOLEAN NOT NULL DEFAULT true;
    ALTER TABLE memory_entities ALTER COLUMN made_before_name_gate SET DEFAULT false;
    """
    + _NAMES_READ.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
        "'id', e.id, 'name', e.name,",
        # the same rule as `hidden_old_pronoun`: never the DM's own entry
        "'id', e.id, 'name', e.name,"
        " 'old', (e.made_before_name_gate AND e.source IS DISTINCT FROM 'dm'),",
    )
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_names_read(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    """
)

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
WEB_RELATIONSHIPS = r"""
    -- The relationship web on the website (#2323): the names within N steps of one name, and the
    -- confirmed connections between them, for the page that draws them. Read only; the words for a
    -- connection are made by the API from the predicates, never here.
    --
    -- Who sees what: a DM of the campaign gets everything confirmed, secret connections and
    -- names included (flagged). Any other member of the server gets the players' view: no entry
    -- the players don't know yet or that is limited to some of them, no thread-like kinds, no
    -- name whose own spelling is secret, no secret connection. The same rule as the All names
    -- page. NULL when the caller is not in the server or there is no such campaign;
    -- {status: 'not_found'} for a centre the caller may not see.
    --
    -- `center_`: the name to centre on, or NULL for the most connected one. `hops`: 1 and up.
    -- `cap`: the most names returned; past it `truncated` is true. `size_only`: just how many names
    -- are within the steps (before the cap).
    CREATE FUNCTION dmbot_web_relationships(
        g BIGINT, c TEXT, center_ TEXT, hops INT, cap INT, size_only BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        vis TEXT[];
        rel_ids TEXT[];
        start_id TEXT := center_;
        within TEXT[];
        total INT;
        kept TEXT[];
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        vis := ARRAY(
            WITH secret_own AS (
                SELECT DISTINCT a.entity_id
                FROM memory_aliases a
                JOIN memory_entities en ON en.guild_id = a.guild_id
                                       AND en.campaign_id = a.campaign_id AND en.id = a.entity_id
                WHERE a.guild_id = g AND a.campaign_id = c AND a.secret AND a.status <> 'rejected'
                  AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                    = regexp_replace(lower(en.name), '[^[:alnum:]]', '', 'g')
            )
            SELECT e.id FROM memory_entities e
            WHERE e.guild_id = g AND e.campaign_id = c
              AND e.status = 'confirmed' AND e.type <> 'person'
              AND (is_dm OR (NOT e.audience_limited AND NOT e.hidden_from_players
                             AND e.type NOT IN ('thread', 'quest', 'mystery', 'hook', 'goal',
                                                'promise', 'deed')
                             AND e.id NOT IN (SELECT s.entity_id FROM secret_own s))));
        -- The connections the caller may see, worked out once (every use below reads this list).
        -- A member sees what the NPC pages show (memory/recall.py): no secret one, none that has
        -- ended, none that came from the story plan. A DM sees all the confirmed ones.
        rel_ids := ARRAY(
            SELECT r.id FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.status = 'confirmed'
              AND r.predicate <> 'led_to'
              AND r.subject_id = ANY (vis) AND r.object_id = ANY (vis)
              AND (is_dm OR (NOT r.secret AND r.to_session_at IS NULL
                             AND r.source NOT IN ('plan', 'story_plan'))));
        IF start_id IS NULL THEN
            SELECT x.id INTO start_id FROM (
                SELECT e.id, e.name,
                       (SELECT count(*) FROM memory_relations r
                        WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                          AND (r.subject_id = e.id OR r.object_id = e.id)) AS degree
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (vis)) x
            ORDER BY x.degree DESC, lower(x.name), x.id LIMIT 1;
        ELSIF NOT (start_id = ANY (vis)) THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF start_id IS NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'center', NULL, 'canEdit', is_dm,
                                      'names', 0, 'nodes', '[]'::jsonb, 'edges', '[]'::jsonb,
                                      'truncated', false);
        END IF;
        WITH RECURSIVE adj AS (
            -- each connection both ways, so the walk joins on one column (no OR)
            SELECT r.subject_id AS a, r.object_id AS b FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
            UNION ALL
            SELECT r.object_id, r.subject_id FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
        ), walk(id, depth) AS (
            SELECT start_id, 0
            UNION
            SELECT adj.b, w.depth + 1
            FROM walk w JOIN adj ON adj.a = w.id
            WHERE w.depth < hops
        )
        SELECT ARRAY(SELECT x.id FROM (SELECT w.id, min(w.depth) AS d FROM walk w GROUP BY w.id) x
                     JOIN memory_entities e ON e.guild_id = g AND e.campaign_id = c AND e.id = x.id
                     ORDER BY x.d, lower(e.name), x.id)
        INTO within;
        total := COALESCE(array_length(within, 1), 0);
        IF size_only THEN
            RETURN jsonb_build_object('status', 'ok', 'names', total);
        END IF;
        kept := within[1:GREATEST(cap, 1)];
        RETURN jsonb_build_object(
            'status', 'ok', 'center', start_id, 'canEdit', is_dm, 'names', total,
            'truncated', total > GREATEST(cap, 1),
            'nodes', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', e.id, 'name', e.name,
                    'kind', CASE WHEN e.type = 'character' THEN
                                CASE e.role WHEN 'player_character' THEN 'player_character'
                                            WHEN 'npc' THEN 'npc' WHEN 'god' THEN 'deity'
                                            ELSE 'character' END
                            ELSE e.type END,
                    'secret', CASE WHEN is_dm THEN EXISTS (
                        SELECT 1 FROM memory_aliases a
                        WHERE a.guild_id = g AND a.campaign_id = c AND a.entity_id = e.id
                          AND a.status <> 'rejected' AND a.secret
                          AND regexp_replace(lower(a.text), '[^[:alnum:]]', '', 'g')
                            = regexp_replace(lower(e.name), '[^[:alnum:]]', '', 'g')) END)
                    ORDER BY array_position(kept, e.id))
                FROM memory_entities e
                WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (kept)), '[]'::jsonb),
            'edges', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', r.id, 'a', r.subject_id, 'b', r.object_id, 'predicate', r.predicate,
                    'detail', r.detail, 'secret', r.secret, 'at', r.created_at)
                    ORDER BY r.created_at, r.id)
                FROM memory_relations r
                WHERE r.guild_id = g AND r.campaign_id = c AND r.id = ANY (rel_ids)
                  AND r.subject_id = ANY (kept) AND r.object_id = ANY (kept)), '[]'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_relationships(BIGINT, TEXT, TEXT, INT, INT, BOOLEAN)
        FROM PUBLIC;
"""

WEB_LINK_ACTIONS = r"""
    -- Edits to the connections on the relationship web (#2323), through the same queue as the All
    -- names page's actions: the website queues them, the bot applies them through the same
    -- MemoryStore calls as Discord's name card (so the change log and Undo agree) and the page
    -- polls. New actions: 'link-add' (`entity_id` and `target_id` are the two names, `text` the
    -- words, `flag` secret), 'link-words' (`target_id` is the connection, `text` the new words),
    -- 'link-secret' (`target_id`, `flag`) and 'link-remove' (`target_id`, `flag` the confirmation).
    -- `result_other` is the other end of the pair the page shows afterwards (`result_id` is the
    -- first), so it can draw the edge as it now stands. No row changes: a new column with a
    -- constant default, and a wider list of actions.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove',
         'link-add', 'link-words', 'link-secret', 'link-remove'));
    ALTER TABLE name_actions ADD COLUMN result_other TEXT NOT NULL DEFAULT ''
        CHECK (char_length(result_other) <= 32);

    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(
        g BIGINT, c TEXT, ent TEXT, act TEXT, kd TEXT, tgt TEXT, txt TEXT, fl BOOLEAN
    ) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        clean TEXT := btrim(regexp_replace(COALESCE(txt, ''), '[[:space:]]+', ' ', 'g'));
        subject TEXT := ent;
        is_link BOOLEAN := act LIKE 'link-%';
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF act IS NULL OR act NOT IN
               ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'remove',
                'link-add', 'link-words', 'link-secret', 'link-remove')
        THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF clean ~ '[[:cntrl:]]' OR char_length(clean) > 100
           OR (act IN ('spell', 'add-other', 'remove-other', 'secret', 'link-add', 'link-words')
               AND clean = '')
           OR (act IN ('kind', 'hide', 'merge', 'remove', 'link-secret', 'link-remove')
               AND clean <> '')
           OR (act = 'kind' AND (kd IS NULL OR kd !~ '^[a-z][a-z _-]{0,39}$'))
           OR (act <> 'kind' AND COALESCE(kd, '') <> '')
           OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove')
               AND (tgt IS NULL OR tgt !~ '^[0-9a-f]{32}$'))
           OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove')
               AND COALESCE(tgt, '') <> '')
           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)
           OR (act = 'link-words' AND COALESCE(fl, false)) THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        IF act IN ('remove', 'link-remove') AND fl IS NOT TRUE THEN
            RETURN jsonb_build_object('status', 'needs_confirm');
        END IF;
        IF is_link AND act <> 'link-add' THEN
            -- an edit to a connection (words, secret, remove, pin): it must be a confirmed one
            -- of this campaign, between two
            -- confirmed names; the entry the action is filed under is its first name
            SELECT r.subject_id INTO subject FROM memory_relations r
            WHERE r.guild_id = g AND r.campaign_id = c AND r.id = tgt AND r.status = 'confirmed'
              AND r.predicate <> 'led_to'
              AND EXISTS (SELECT 1 FROM memory_entities x WHERE x.guild_id = g
                          AND x.campaign_id = c AND x.id = r.subject_id
                          AND x.status = 'confirmed' AND x.type <> 'person')
              AND EXISTS (SELECT 1 FROM memory_entities y WHERE y.guild_id = g
                          AND y.campaign_id = c AND y.id = r.object_id
                          AND y.status = 'confirmed' AND y.type <> 'person');
            IF NOT FOUND THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
        ELSIF subject IS NULL OR NOT EXISTS (
               SELECT 1 FROM memory_entities e
               WHERE e.guild_id = g AND e.campaign_id = c AND e.id = subject
                 AND e.status = 'confirmed' AND e.type <> 'person') THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF act IN ('merge', 'link-add') AND (tgt = subject OR NOT EXISTS (
               SELECT 1 FROM memory_entities t
               WHERE t.guild_id = g AND t.campaign_id = c AND t.id = tgt
                 AND t.status = 'confirmed' AND t.type <> 'person')) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('name_action:' || g::TEXT || c, 0));
        DELETE FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 3600;
        IF (SELECT count(*) FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 30
        THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        INSERT INTO name_actions (guild_id, campaign_id, id, entity_id, user_id, action, kind,
                                  target_id, text, flag, created_at)
            VALUES (g, c, rid, subject, dmbot_current_user(), act, COALESCE(kd, ''),
                    COALESCE(tgt, ''), clean, COALESCE(fl, false), now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r name_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM name_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user();
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'error', r.error, 'action', r.action,
                                  'resultId', r.result_id, 'resultOther', r.result_other);
    END
    $fn$;
    -- (redefined functions lose their pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

# Merging several names at once (#2426, migration 0183). Three things:
# 1. the website's All names queue takes `merge-many`: the kept name is the row's entry and `text`
#    holds the other names' ids, space apart (at most 50 of 32 letters), so the column is wider for
#    that one action only;
# 2. the DM's "Not the same" for two names, remembered so the look-alike finder never offers the
#    pair again (the DM's alone; never in backups, recall, transcripts or any player view);
# 3. the All names list says how many times each name was heard (`mentions`), so the page can put
#    the most used first under "Keep which name?".
_NAME_ACTION_DOOR = WEB_LINK_ACTIONS[
    WEB_LINK_ACTIONS.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(") : (
        WEB_LINK_ACTIONS.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_read")
    )
]

_MANY = "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'"

_WITH_MANY = (
    "('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',"
    " 'remove',\n         'link-add', 'link-words', 'link-secret', 'link-remove')"
)

assert "'merge', 'remove',\n                'link-add'" in _NAME_ACTION_DOOR

MERGE_MANY = (
    r"""
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        """
    + _WITH_MANY
    + r""");
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check
        CHECK (char_length(text) <= CASE WHEN action = 'merge-many' THEN 1700 ELSE 100 END);
    """
    + _NAME_ACTION_DOOR.replace(
        "'merge', 'remove',\n                'link-add'",
        "'merge', 'merge-many', 'remove',\n                'link-add'",
    )
    .replace(
        "char_length(clean) > 100",
        # (in brackets: plpgsql would take the CASE's THEN for the end of the IF's test)
        "char_length(clean) > (CASE WHEN act = 'merge-many' THEN 1700 ELSE 100 END)",
    )
    .replace(
        "           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)",
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~ " + _MANY + "))\n"
        "           OR (act IN ('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL)",
        1,
    )
    .replace(
        "        PERFORM pg_advisory_xact_lock(",
        """        IF act = 'merge-many' AND (
               ent = ANY (string_to_array(clean, ' '))
               OR (SELECT count(DISTINCT x) FROM unnest(string_to_array(clean, ' ')) x)
                  <> cardinality(string_to_array(clean, ' '))
               OR (SELECT count(*) FROM memory_entities t
                   WHERE t.guild_id = g AND t.campaign_id = c
                     AND t.id = ANY (string_to_array(clean, ' '))
                     AND t.status = 'confirmed' AND t.type <> 'person')
                  <> cardinality(string_to_array(clean, ' '))) THEN
            RETURN jsonb_build_object('status', 'bad_target');
        END IF;
        PERFORM pg_advisory_xact_lock(""",
        1,
    )
    + f"""
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;

    -- Two names the DM said are not the same: `a_id` sorts before `b_id`, so a pair is one row.
    -- Gone with either name (and its campaign); isolated per server; not in backups.
    CREATE TABLE name_not_same (
        {_memory_scope()}
        a_id        TEXT NOT NULL,
        b_id        TEXT NOT NULL,
        decided_by  BIGINT NOT NULL CHECK (decided_by > 0),
        created_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, a_id, b_id),
        CHECK (a_id < b_id),
        {_entity_link("a_id")},
        {_entity_link("b_id")}
    );
    CREATE INDEX name_not_same_b ON name_not_same (guild_id, campaign_id, b_id);
    """
    + _isolate("name_not_same")
)

_MENTIONS_OF = """'hidden', p.hidden,
                    'mentions', (SELECT count(*) FROM memory_mentions m
                                 WHERE m.guild_id = g AND m.campaign_id = c
                                   AND m.entity_id = p.id),"""

assert ALL_NAMES_SPEED.count("'hidden', p.hidden,") == 1

ALL_NAMES_MENTIONS = ALL_NAMES_SPEED.replace("'hidden', p.hidden,", _MENTIONS_OF, 1)

# The Names page's type filter takes several kinds at once (owner request, 2026-10-27): the
# door's `k` is a comma-separated list of kinds, and a name is kept when its kind is any of them.
# A one-kind list is what it took before, so every caller keeps working. (A redefined function loses
# its pinned settings, so the copy of the door's tail that sets them again comes with it.)
assert ALL_NAMES_MENTIONS.count("(kind_ IS NULL OR v.kind = kind_)") == 1

# The door also bounds the page itself (no website route asks for more than 500, but a door is
# asked by whoever holds the role): `LEAST(GREATEST(size, 1), 500)` wherever the size is used.
assert ALL_NAMES_MENTIONS.count("GREATEST(size, 1)") == 2

ALL_NAMES_KINDS = ALL_NAMES_MENTIONS.replace(
    "(kind_ IS NULL OR v.kind = kind_)",
    "(kind_ IS NULL OR v.kind = ANY (string_to_array(kind_, ',')))",
    1,
).replace("GREATEST(size, 1)", "LEAST(GREATEST(size, 1), 500)")

# Search by meaning on the Names page (#2733, migration 0253).
# `only_id` takes a short comma-separated list of ids as well as one (entry ids are 32 hex digits,
# so a comma never belongs to one): the page asks for the rows of the names the search found, in
# the rows' usual shape, with one call instead of one for each name.
assert ALL_NAMES_KINDS.count("(only_id IS NULL OR v.id = only_id)") == 1

# The relationship web's door reads what The party believes (#2409 part B): a member sees the
# connections the players hold as fact, where it used to test the `secret` flag. Until guesses
# arrive the two always agree (the writers keep them so, and a test holds the door to it). The
# door is redefined as it was, with that one test changed, and its pinned settings set again.
_PARTY_KNOWS_R = (
    "EXISTS (SELECT 1 FROM memory_beliefs pb JOIN memory_perspectives pp"
    " ON pp.guild_id = pb.guild_id AND pp.campaign_id = pb.campaign_id"
    " AND pp.id = pb.perspective_id WHERE pp.kind = 'party' AND pb.guild_id = r.guild_id"
    " AND pb.campaign_id = r.campaign_id AND pb.relation_id = r.id AND pb.confidence >= 1.0"
    " AND pb.seq = (SELECT max(x.seq) FROM memory_beliefs x WHERE x.guild_id = pb.guild_id"
    " AND x.campaign_id = pb.campaign_id AND x.perspective_id = pb.perspective_id"
    " AND x.relation_id = pb.relation_id))"
)

assert WEB_RELATIONSHIPS.count("(is_dm OR (NOT r.secret AND r.to_session_at IS NULL") == 1

# (#2790) Written before unnamed people existed: migration 0264 adds their guard to the function as
# it is in the database. A migration that builds it again from this text must add the guard too;
# tests/test_hidden_guards.py fails when a function is left without it.
WEB_GRAPH_PARTY = WEB_RELATIONSHIPS.replace(
    "CREATE FUNCTION dmbot_web_relationships(",
    "CREATE OR REPLACE FUNCTION dmbot_web_relationships(",
    1,
).replace(
    "(is_dm OR (NOT r.secret AND r.to_session_at IS NULL",
    "(is_dm OR (" + _PARTY_KNOWS_R + " AND r.to_session_at IS NULL",
    1,
)

_VIEW_STEPS = (
    # (old, new, how many times the old text is in the door)
    (
        "        is_dm BOOLEAN;\n        vis TEXT[];",
        "        is_dm BOOLEAN;\n        sees_all BOOLEAN;\n        wanted TEXT;\n"
        "        screen TEXT;\n        vis TEXT[];",
        1,
    ),
    (
        "                           AND d.user_id = dmbot_current_user());\n        vis := ARRAY(",
        """                           AND d.user_id = dmbot_current_user());
        SELECT dm_screen_visibility INTO screen FROM campaigns WHERE guild_id = g AND id = c;
        wanted := COALESCE(NULLIF(current_setting('dmbot.web_view', true), ''),
                           CASE WHEN is_dm THEN 'full' ELSE 'players' END);
        -- A DM sees everything unless they asked for Players View; anyone else sees the players'
        -- view unless they asked to peek and the DM screen lets them.
        sees_all := wanted = 'full' AND (is_dm OR screen IN ('peek', 'open'));
        IF wanted = 'full' AND NOT sees_all THEN
            RETURN jsonb_build_object('status', 'peek_off');
        END IF;
        -- A player's full view is a peek, however it was asked for: the DM is told once for this
        -- visit (the visit is the API's `dmbot.web_visit`); with none, the peek is refused.
        IF sees_all AND NOT is_dm
           AND dmbot_web_peek_note(g, c, current_setting('dmbot.web_visit', true)) IS NULL THEN
            RETURN jsonb_build_object('status', 'bad_visit');
        END IF;
        vis := ARRAY(""",
        1,
    ),
    ("AND (is_dm OR (NOT e.audience_limited", "AND (sees_all OR (NOT e.audience_limited", 1),
    (
        "              AND (is_dm OR (" + _PARTY_KNOWS_R,
        "              AND (sees_all OR (" + _PARTY_KNOWS_R,
        1,
    ),
    (
        "'canEdit', is_dm,",
        "'canEdit', is_dm AND sees_all, 'seesAll', sees_all,\n"
        "                                      'view',\n"
        "                                      CASE WHEN sees_all THEN 'full' ELSE 'players' END,\n"
        "                                      'visibility', screen,\n"
        "                                      'canPeek', is_dm OR screen IN ('peek', 'open'),",
        2,
    ),
    ("'secret', CASE WHEN is_dm THEN EXISTS (", "'secret', CASE WHEN sees_all THEN EXISTS (", 1),
)

_door = WEB_GRAPH_PARTY

_door__2 = _door
for _old, _new, _times in _VIEW_STEPS:
    assert _door__2.count(_old) == _times, _old
    _door__2 = _door__2.replace(_old, _new)

# A connection the DM's story holds back from the players until it is said at the table (#2404):
# a backstory connection starts unrevealed. The column has a default and is added, not updated,
# so every existing connection reads as revealed and no row-level security needs opening. The
# party's belief in it (`beliefs.follow`) stays the one thing the players' readers look at; this
# flag only decides whether `follow` gives the party that belief. The graph door also says, for
# the full view, which names and connections the players have not been shown yet.
UNREVEALED_CONNECTIONS = """
    ALTER TABLE memory_relations ADD COLUMN unrevealed BOOLEAN NOT NULL DEFAULT false;
    """ + (
    _door__2.replace(
        "'id', e.id, 'name', e.name,\n",
        "'id', e.id, 'name', e.name,\n"
        "                    'revealed', NOT (e.hidden_from_players OR e.audience_limited),\n",
        1,
    ).replace(
        "'detail', r.detail, 'secret', r.secret, 'at', r.created_at)",
        "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,\n"
        "                    'revealed', " + _PARTY_KNOWS_R + ")",
        1,
    )
)

assert UNREVEALED_CONNECTIONS.count("'revealed', NOT (e.hidden") == 1

assert UNREVEALED_CONNECTIONS.count("'revealed', EXISTS") == 1

# The DM's 📌 100% truth (#2447): a flag on The truth's belief in a connection (a new belief row
# each time it is set or cleared, so the change log and Undo work as for any belief), and the flag
# a connection waiting from a campaign-lore upload carries until the DM keeps it. Both columns have
# defaults, so no row changes. The relationship web's door tells a DM, and only a DM seeing
# everything, which connections are pinned.
_PINNED_LINK = (
    "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,\n"
    "                    'revealed', " + _PARTY_KNOWS_R + ")"
)

_door_pinned = UNREVEALED_CONNECTIONS[
    UNREVEALED_CONNECTIONS.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships") :
]

assert _door_pinned.count(_PINNED_LINK) == 1

PINNED_TRUTH = """
    ALTER TABLE memory_beliefs ADD COLUMN pinned BOOLEAN NOT NULL DEFAULT false;
    ALTER TABLE backstory_links ADD COLUMN truth BOOLEAN NOT NULL DEFAULT false;
    """ + _door_pinned.replace(
    _PINNED_LINK,
    _PINNED_LINK[:-1] + ",\n                    'pinned', (is_dm AND sees_all) AND EXISTS"
    " (SELECT 1 FROM memory_beliefs tb"
    " JOIN memory_perspectives tp ON tp.guild_id = tb.guild_id"
    " AND tp.campaign_id = tb.campaign_id AND tp.id = tb.perspective_id"
    " WHERE tp.kind = 'truth' AND tb.guild_id = r.guild_id AND tb.campaign_id = r.campaign_id"
    " AND tb.relation_id = r.id AND tb.pinned AND tb.seq = (SELECT max(x.seq)"
    " FROM memory_beliefs x WHERE x.guild_id = tb.guild_id AND x.campaign_id = tb.campaign_id"
    " AND x.perspective_id = tb.perspective_id AND x.relation_id = tb.relation_id)))",
    1,
)

# Family roles (#2457, Supervisor's ruling A): whether a family connection's detail is a role that
# reads one way ("Mira is the niece of Aldric"). Older rows are false: their detail was stored with
# the two names sorted by id, so its direction is arbitrary and they keep reading "is family of".
# The column has a default and is added, not updated, so no row changes and no backfill policy.
# The website's door sends it with each connection, built from the door as it stands now (0190).
_DIRECTIONAL_LINK = "'detail', r.detail, 'secret', r.secret, 'at', r.created_at,"

_door_directional = PINNED_TRUTH[
    PINNED_TRUTH.index("CREATE OR REPLACE FUNCTION dmbot_web_relationships") :
]

assert _door_directional.count(_DIRECTIONAL_LINK) == 1

RELATION_DIRECTIONAL = """
    ALTER TABLE memory_relations ADD COLUMN directional BOOLEAN NOT NULL DEFAULT false;
    """ + _door_directional.replace(
    _DIRECTIONAL_LINK,
    "'detail', r.detail, 'directional', r.directional, 'secret', r.secret, 'at', r.created_at,",
    1,
)

# Built from the door as it stands now, with `merge-many` (0183), not from the older text: a door
# built from before that would drop it (found in the review of #2456).
_door_start = MERGE_MANY.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(")

_door_end = MERGE_MANY.index("    DO $do$", _door_start)

_pin_door = MERGE_MANY[_door_start:_door_end]

_pin_door__2 = _pin_door
for _old, _new, _times in (
    (
        "'link-add', 'link-words', 'link-secret', 'link-remove')",
        "'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        3,
    ),
    (
        "'hide', 'merge', 'remove', 'link-secret', 'link-remove')",
        "'hide', 'merge', 'remove', 'link-secret', 'link-remove', 'link-pin')",
        1,
    ),
):
    assert _pin_door__2.count(_old) == _times, (_old, _pin_door__2.count(_old))
    _pin_door__2 = _pin_door__2.replace(_old, _new)

assert _pin_door__2.count("('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL") == 1

_pin_door__3 = _pin_door__2.replace(
    "('secret', 'hide', 'link-add', 'link-secret') AND fl IS NULL",
    "('secret', 'hide', 'link-add', 'link-secret', 'link-pin') AND fl IS NULL",
)

WAITING_ACTIONS = (
    "claim-confirm", "claim-reject", "backstory-keep", "backstory-skip", "backstory-keep-all",
)  # fmt: skip

_WAITING_LIST = ", ".join(f"'{a}'" for a in WAITING_ACTIONS)

_WAITING_NEED_TARGET = ", ".join(f"'{a}'" for a in WAITING_ACTIONS[:4])  # keep-all has none

_KEEP_ALL_IDS = "'^[0-9a-f]{32}( [0-9a-f]{32}){0,49}$'"  # Keep all names the ids the page showed

LINK_DECIDE_ACTIONS = (
    """
    -- The website's answers to the connections that wait for the DM (#2635, #2642), through the
    -- queue of the other name actions. PlotBot's waiting connections are of two kinds, and neither
    -- is a proposed row of `memory_relations`:
    --   'claim-confirm' / 'claim-reject': `target_id` is a waiting `memory_claims` row (said at the
    --       table); `flag` on a confirm is "truth but secret". The bot makes the same call as the
    --       DM screen's ✅ and ❌ (`ClaimStore.confirm` / `reject`), so consent, the plan and
    --       instruction checks, the DM's own word and Undo are the same.
    --   'backstory-keep' / 'backstory-skip': `target_id` is a waiting `backstory_links` row (from
    --       the DM's own backstory); `flag` on a keep is "keep as secret". 'backstory-keep-all'
    --       keeps every one that is ready (no target). The bot keeps them as the one-by-one cards
    --       do (the DM's word, unrevealed), and answers how many in `result_id`.
    -- `entity_id` is empty for these: they belong to no one name. A backstory link gets an id of
    -- its own, so the page and the queue can point at one. No existing row changes (the column has
    -- a default, filled for old rows by the table rewrite).
    ALTER TABLE backstory_links
        ADD COLUMN id TEXT NOT NULL DEFAULT replace(gen_random_uuid()::TEXT, '-', '')
            CHECK (id ~ '^[0-9a-f]{32}$');
    CREATE UNIQUE INDEX backstory_links_id ON backstory_links (guild_id, campaign_id, id);
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         """
    + _WAITING_LIST
    + """));
    -- Keep all carries the ids of the connections the page showed (at most 50 of 32 letters, space
    -- apart), so a link that turned ready while the DM was looking is not kept unseen.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_text_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_text_check CHECK (char_length(text) <=
        CASE WHEN action IN ('merge-many', 'backstory-keep-all') THEN 1700 ELSE 100 END);
"""
)

# Built from the door as it stands now (with `link-pin`, 0193), not from an older text: a door
# built from before would drop the actions added since (the lesson of #2456).
_decide_door = _pin_door__3

_waiting_check = """        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' THEN
            -- a waiting connection: it must still be waiting in this campaign (the bot checks
            -- again when it applies it, and both of its ends are confirmed names then)
            subject := '';
            IF act LIKE 'claim-%' AND NOT EXISTS (
                   SELECT 1 FROM memory_claims m WHERE m.guild_id = g AND m.campaign_id = c
                   AND m.id = tgt AND m.status = 'proposed') THEN
                RETURN jsonb_build_object('status', 'not_found');
            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (
                   SELECT 1 FROM backstory_links b WHERE b.guild_id = g AND b.campaign_id = c
                   AND b.id = tgt) THEN
                RETURN jsonb_build_object('status', 'not_found');
            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (
                   SELECT 1 FROM backstory_links b WHERE b.guild_id = g AND b.campaign_id = c
                   AND b.id = ANY (string_to_array(clean, ' '))) THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
        ELSIF is_link AND act <> 'link-add' THEN"""

_decide_door__2 = _decide_door
for _old, _new in (
    (
        "                'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        f"                'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        f"                {_WAITING_LIST})",
    ),
    (
        "'link-secret', 'link-remove', 'link-pin')\n               AND clean <> '')",
        f"'link-secret', 'link-remove', 'link-pin',\n"
        f"                       {_WAITING_NEED_TARGET})\n               AND clean <> '')",
    ),
    (
        "OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin')",
        "OR (act IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',\n"
        f"                       {_WAITING_NEED_TARGET})",
    ),
    (
        "OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', "
        "'link-pin')",
        "OR (act NOT IN ('merge', 'link-add', 'link-words', 'link-secret', 'link-remove', "
        f"'link-pin',\n                       {_WAITING_NEED_TARGET})",
    ),
    (
        "('secret', 'hide', 'link-add', 'link-secret', 'link-pin') AND fl IS NULL",
        "('secret', 'hide', 'link-add', 'link-secret', 'link-pin', 'claim-confirm',\n"
        "                       'backstory-keep') AND fl IS NULL",
    ),
    ("        IF is_link AND act <> 'link-add' THEN", _waiting_check),
    (
        "char_length(clean) > (CASE WHEN act = 'merge-many' THEN 1700 ELSE 100 END)",
        "char_length(clean) > (CASE WHEN act IN ('merge-many', 'backstory-keep-all')"
        " THEN 1700 ELSE 100 END)",
    ),
    (
        "OR (act = 'merge-many' AND (clean = '' OR clean !~",
        "OR (act = 'backstory-keep-all' AND (clean = '' OR clean !~ " + _KEEP_ALL_IDS + "))\n"
        "           OR (act = 'merge-many' AND (clean = '' OR clean !~",
    ),
):
    assert _decide_door__2.count(_old) == 1, (_old, _decide_door__2.count(_old))
    _decide_door__2 = _decide_door__2.replace(_old, _new)

LINK_DECIDE_ACTIONS__2 = (
    LINK_DECIDE_ACTIONS
    + _decide_door__2
    + """
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;
"""
)

HANDOVER_CLAIM = """
    -- Opening a hand-over link on the website (#2532, slice B). The link names the server and the
    -- offer and carries a secret whose SHA-256 the offer holds, so the door can read the one offer
    -- in the one server (every server table is scoped by server) and nothing else. It answers only
    -- for a signed-in person, and only for an offer that is still open, by email, unexpired, not
    -- yet claimed by someone else, and whose campaign still belongs to the person who offered it.
    -- Anything else is NULL, the same whatever the reason, so a link says nothing about why it
    -- ended. With p_claim the offer becomes this person's (their id and name are written once; the
    -- link then works for no one else), unless they are the owner. The rest of the answer (plan,
    -- slot, ownership) is the campaign store's own accept, with the person's session as always.
    -- The caller connects scoped to the server `g` (the door refuses any other).
    CREATE FUNCTION dmbot_handover_open(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT, p_claim BOOLEAN) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        r RECORD;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN NULL;
        END IF;
        IF p_claim THEN  -- two claims at once: one waits for the other, then sees it taken
            PERFORM 1 FROM campaign_handover_offers WHERE guild_id = g AND id = o FOR UPDATE;
        END IF;
        SELECT f.campaign_id, f.from_user_id, f.from_name, f.to_user_id, f.created_at,
               c.name AS campaign_name
            INTO r
            FROM campaign_handover_offers f
            JOIN campaigns c ON c.guild_id = f.guild_id AND c.id = f.campaign_id
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me)
              AND c.owner_user_id = f.from_user_id;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        IF p_claim AND r.from_user_id <> me AND r.to_user_id = 0 THEN
            UPDATE campaign_handover_offers
                SET to_user_id = me, to_name = left(coalesce(p_name, ''), 80)
                WHERE guild_id = g AND id = o;
        END IF;
        RETURN jsonb_build_object(
            'campaign_id', r.campaign_id, 'campaign_name', r.campaign_name,
            'from_user_id', r.from_user_id, 'from_name', r.from_name,
            'expires_at', r.created_at + 604800);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_open(BIGINT, BIGINT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;

    -- Gives a claim back (#2532): an accept that fails after the link was claimed (no room any
    -- more, the campaign changed hands) must not leave the link dead for the person it was for.
    -- Only the person who holds the claim, only on an offer still open and still unanswered.
    CREATE FUNCTION dmbot_handover_release(g BIGINT, o BIGINT, p_hash TEXT) RETURNS VOID
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN;
        END IF;
        UPDATE campaign_handover_offers SET to_user_id = 0, to_name = ''
            WHERE guild_id = g AND id = o AND via_email AND token_hash = p_hash
              AND status = 'open' AND to_user_id = me;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_release(BIGINT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_release(BIGINT, BIGINT, TEXT) FROM PUBLIC;

    -- "No thanks" from whoever holds the link, wherever they are (#2532): the website's role can
    -- write an answer only in the servers of its session, and the person who was emailed is often
    -- not in the campaign's server. Same checks as dmbot_handover_open; the offer is declined
    -- under their id and name, and the bot is told (as for every answer made on the website) so
    -- the owner hears. NULL, the same as any ended link; the owner cannot decline their own.
    CREATE FUNCTION dmbot_handover_decline(
        g BIGINT, o BIGINT, p_hash TEXT, p_name TEXT) RETURNS BOOLEAN
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        me BIGINT := dmbot_current_user();
        changed INTEGER;
    BEGIN
        IF me IS NULL OR g IS NULL OR o IS NULL OR p_hash IS NULL OR p_hash !~ '^[0-9a-f]{64}$'
           OR current_setting('dmbot.guild_id', true) IS DISTINCT FROM g::text THEN
            RETURN false;
        END IF;
        UPDATE campaign_handover_offers f
            SET status = 'declined', decided_at = dmbot_now(), to_user_id = me,
                to_name = left(coalesce(p_name, ''), 80)
            FROM campaigns c
            WHERE f.guild_id = g AND f.id = o AND f.via_email AND f.token_hash = p_hash
              AND f.status = 'open' AND f.created_at + 604800 > dmbot_now()
              AND (f.to_user_id = 0 OR f.to_user_id = me) AND f.from_user_id <> me
              AND c.guild_id = f.guild_id AND c.id = f.campaign_id
              AND c.owner_user_id = f.from_user_id;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed = 1 THEN
            PERFORM pg_notify('dmbot_handover_decided', g::text || ':' || o::text);
        END IF;
        RETURN changed = 1;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_handover_decline(BIGINT, BIGINT, TEXT, TEXT) FROM PUBLIC;
"""

# A campaign moves to another server by changing `guild_id` on its `campaigns` row (#2532, slice
# C2); every foreign key that carries `guild_id` must follow it, or the move would be refused for
# the first child row or leave rows behind. Rebuilt from the catalog so no table is forgotten
# (tests/test_campaign_move_cascade.py checks it still holds). Dropping a key takes an exclusive
# lock on its table, and the migration is one transaction, so those locks last until it ends: deploy
# it with the bot stopped (docs/testing-status.log). Each key is added NOT VALID and then validated.
# Every foreign key that carries `guild_id` becomes ON UPDATE CASCADE (found from the catalog, so
# no table is forgotten). Run by 0215 and again by 0234: a table made between them (0225's
# `recap_upload_rights`) was missed the first time. Already-cascading keys are left alone.
_CASCADE_ALL_KEYS = """
    DO $$
    DECLARE r record;
    BEGIN
        FOR r IN
            -- ON UPDATE has to come before DEFERRABLE, so it is put in ahead of it.
            SELECT c.conrelid::regclass AS tbl, c.conname,
                   regexp_replace(pg_get_constraintdef(c.oid),
                                  '( (NOT )?DEFERRABLE.*)?$', ' ON UPDATE CASCADE\\1') AS def
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                 AND a.attname = 'guild_id'
            WHERE c.contype = 'f' AND n.nspname = current_schema() AND c.confupdtype = 'a'
        LOOP
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I %s NOT VALID',
                r.tbl, r.conname, r.def);
            EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', r.tbl, r.conname);
        END LOOP;
    END $$;
"""


def _approve_with_versions() -> str:
    """`dmbot_recap_approve` (RECAP_APPROVAL) as it was, plus a call to `dmbot_recap_keep_version`
    (#2122). Taken from the earlier migration's text, so the two cannot drift apart."""
    start = RECAP_APPROVAL.index("    CREATE FUNCTION dmbot_recap_approve(")
    end = RECAP_APPROVAL.index("    $fn$;", start) + len("    $fn$;")
    body = RECAP_APPROVAL[start:end].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    declared = "        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;\n"
    keep = "        RETURN 'approved';\n    END"
    if declared not in body or keep not in body:  # (an earlier migration's text moved)
        raise RuntimeError("dmbot_recap_approve changed: the version step needs a new anchor")
    return body.replace(
        keep,
        "        -- The text the DM approved becomes the session's summary in the campaign notes.\n"
        "        PERFORM dmbot_recap_keep_version(g, c, at_, dmbot_current_user(), now_s);\n"
        + keep,
        1,
    )


PLOT_CHAT = (
    f"""
    -- The DM's talk with PlotBot about one plot issue (#2123, docs/PLAN.md, "PlotBot checks every
    -- recap"). The website has no AI key, so the DM's message is queued here as a row, the bot
    -- answers on a second row (with the changes it proposes, if any), and the page polls. The talk
    -- is the DM's own: a row is read by the DM who wrote it and no one else, is never in a
    -- transcript or on a public link, and is never logged. It stays with its issue and goes with
    -- the campaign; it is left out of backups. `proposals`: a list of {{id, kind, label, data,
    -- state}} (state open, saving, saved, declined or failed): nothing is saved until the DM
    -- presses Save. New table: no rows change, so no backfill policy.
    CREATE TABLE plot_chat (
        {_memory_scope()}
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{{32}}$'),
        issue_id    TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        role        TEXT NOT NULL CHECK (role IN ('dm', 'bot')),
        body        TEXT NOT NULL DEFAULT '' CHECK (char_length(body) <= 2000),
        state       TEXT NOT NULL DEFAULT 'done'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        proposals   JSONB NOT NULL DEFAULT '[]'
            CHECK (jsonb_typeof(proposals) = 'array' AND jsonb_array_length(proposals) <= 6),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, campaign_id, id)
    );
    CREATE INDEX plot_chat_by_issue ON plot_chat (guild_id, campaign_id, issue_id, created_at);
    CREATE INDEX plot_chat_open ON plot_chat (guild_id, created_at)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("plot_chat")
    + r"""
    -- The website's role has no grant on the table; these doors each check the caller is a DM of
    -- the campaign in a server on the session's list, and a DM sees only their own messages.
    CREATE FUNCTION dmbot_plot_chat_send(g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, txt TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        iss plot_issues%ROWTYPE;
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        IF txt IS NULL OR char_length(btrim(txt)) = 0 OR char_length(txt) > 1000 THEN
            RETURN jsonb_build_object('status', 'bad_text');
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        SELECT * INTO iss FROM plot_issues
            WHERE guild_id = g AND campaign_id = c AND id = p_issue AND session_at = at_;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('status', 'not_found');
        END IF;
        IF iss.text_hash <> encode(sha256(convert_to(job.title || E'\n' || job.summary, 'UTF8')),
                                   'hex') THEN
            RETURN jsonb_build_object('status', 'stale');
        END IF;
        -- A talk is kept for thirty days; older words of this campaign go here.
        DELETE FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.created_at < now_s - 2592000;
        -- A message the bot never answered (it was off, or died) is given up after ten minutes,
        -- so the DM is never stuck behind it.
        UPDATE plot_chat SET state = 'failed', error = 'timed_out'
            WHERE guild_id = g AND campaign_id = c AND user_id = dmbot_current_user()
              AND role = 'dm' AND state IN ('waiting', 'working') AND created_at < now_s - 600;
        -- One at a time, and a bounded talk: PlotBot is quick, not a chat room.
        IF EXISTS (SELECT 1 FROM plot_chat m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
                     AND m.user_id = dmbot_current_user()
                     AND m.state IN ('waiting', 'working')) THEN
            RETURN jsonb_build_object('status', 'busy');
        END IF;
        IF (SELECT count(*) FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
              AND m.user_id = dmbot_current_user() AND m.role = 'dm') >= 15 THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        -- The campaign's whole hour: at most thirty messages from its DMs (each is a careful-tier
        -- call; docs/costs.md), however many issues they are on.
        IF (SELECT count(*) FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.role = 'dm'
              AND m.created_at > now_s - 3600) >= 30 THEN
            RETURN jsonb_build_object('status', 'too_many');
        END IF;
        INSERT INTO plot_chat (guild_id, campaign_id, id, issue_id, session_at, user_id, role,
                               body, state, created_at)
            VALUES (g, c, rid, p_issue, at_, dmbot_current_user(), 'dm', btrim(txt), 'waiting',
                    now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    CREATE FUNCTION dmbot_plot_chat_read(g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user())
           OR NOT EXISTS (SELECT 1 FROM plot_issues i
                          WHERE i.guild_id = g AND i.campaign_id = c AND i.id = p_issue
                            AND i.session_at = at_) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('messages', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'id', m.id, 'role', m.role, 'text', m.body, 'state', m.state,
                       'error', m.error, 'proposals', m.proposals, 'at', m.created_at)
                   ORDER BY m.created_at, m.role DESC, m.id)
            FROM plot_chat m
            WHERE m.guild_id = g AND m.campaign_id = c AND m.issue_id = p_issue
              AND m.session_at = at_ AND m.user_id = dmbot_current_user()), '[]'::jsonb));
    END
    $fn$;

    -- Save or Not that, on one proposal of PlotBot's. Not that closes it. Save only marks it
    -- 'saving': the bot makes the change, as the DM's own fact, and then marks it saved. A
    -- proposal that is no longer open is 'done_already'.
    CREATE FUNCTION dmbot_plot_proposal(
        g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, p_prop TEXT, p_action TEXT)
    RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        m plot_chat%ROWTYPE;
        want TEXT;
    BEGIN
        IF p_action IS NULL OR p_action NOT IN ('save', 'decline') THEN
            RETURN 'bad';
        END IF;
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_found';
        END IF;
        -- (a proposal's id is a fresh random id, so it names one proposal of one message)
        SELECT * INTO m FROM plot_chat x
            WHERE x.guild_id = g AND x.campaign_id = c AND x.role = 'bot'
              AND x.issue_id = p_issue AND x.session_at = at_
              AND x.user_id = dmbot_current_user()
              AND x.proposals @> jsonb_build_array(jsonb_build_object('id', p_prop))
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        IF NOT m.proposals @> jsonb_build_array(
                jsonb_build_object('id', p_prop, 'state', 'open')) THEN
            RETURN 'done_already';
        END IF;
        -- A proposal is about the words PlotBot read: if the recap changed since, the page
        -- reloads and a new check has its own issues.
        IF p_action = 'save' AND NOT EXISTS (
                SELECT 1 FROM recap_jobs j JOIN plot_issues i
                    ON i.guild_id = j.guild_id AND i.campaign_id = j.campaign_id
                    AND i.session_at = j.session_at
                WHERE j.guild_id = g AND j.campaign_id = c AND j.session_at = at_
                    AND i.id = p_issue
                    AND i.text_hash = encode(sha256(convert_to(j.title || E'\n' || j.summary,
                                                               'UTF8')), 'hex')) THEN
            RETURN 'stale';
        END IF;
        want := CASE p_action WHEN 'save' THEN 'saving' ELSE 'declined' END;
        UPDATE plot_chat SET proposals = (
            SELECT jsonb_agg(CASE WHEN e->>'id' = p_prop
                                  THEN jsonb_set(e, '{state}', to_jsonb(want)) ELSE e END
                             ORDER BY ord)
            FROM jsonb_array_elements(m.proposals) WITH ORDINALITY AS t(e, ord))
            WHERE guild_id = g AND campaign_id = c AND id = m.id;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_plot_chat_send(BIGINT, TEXT, BIGINT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_chat_read(BIGINT, TEXT, BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_proposal(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)
        FROM PUBLIC;
    """
)

WEB_WAITING_ACTIONS_LIST = ", ".join(
    f"'{a}'" for a in (*WAITING_ACTIONS, "backstory-which-pick", "backstory-which-neither")
)

_which_door = LINK_DECIDE_ACTIONS__2[
    LINK_DECIDE_ACTIONS__2.index("    CREATE OR REPLACE FUNCTION dmbot_web_name_action_add(") :
]

_which_door__2 = _which_door[: _which_door.index("    -- (a redefined function loses")]

_which_door__3 = _which_door__2
for _old, _new in (
    (
        "'backstory-skip', 'backstory-keep-all')\n        THEN",
        "'backstory-skip', 'backstory-keep-all',\n"
        "                'backstory-which-pick', 'backstory-which-neither')\n        THEN",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND clean <> '')",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-neither')\n               AND clean <> '')",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND (tgt IS NULL",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND (tgt IS NULL",
    ),
    (
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'claim-confirm', 'claim-reject', 'backstory-keep', 'backstory-skip',\n"
        "                       'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "           OR (act = 'link-words' AND COALESCE(fl, false)) THEN",
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(fl, false))\n"
        "           OR (act = 'link-words' AND COALESCE(fl, false)) THEN",
    ),
    (
        "           OR (act = 'backstory-keep-all' AND (clean = ''",
        "           OR (act = 'backstory-which-pick' AND clean !~ '^[0-9a-f]{32}$')\n"
        "           OR (act = 'backstory-keep-all' AND (clean = ''",
    ),
    (
        "            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (",
        "            ELSIF act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "                   AND NOT EXISTS (\n"
        "                   SELECT 1 FROM backstory_which w WHERE w.guild_id = g\n"
        "                   AND w.campaign_id = c AND w.id = tgt\n"
        "                   AND (act = 'backstory-which-neither'\n"
        "                        OR clean = ANY (w.candidates))) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'backstory-keep-all' AND NOT EXISTS (",
    ),
):
    assert _which_door__3.count(_old) == 1, (_old, _which_door__3.count(_old))
    _which_door__3 = _which_door__3.replace(_old, _new)

WEB_WAITING = (
    f"""
    -- The website's answer to a "Which Mara?" question (#2652): two more actions in the queue of
    -- the other name actions. 'backstory-which-pick': `target_id` is the waiting question
    -- (`backstory_which`), `text` the entry the DM picked (one of its candidates); and
    -- 'backstory-which-neither': the question is dropped. The bot applies them with
    -- `BackstoryLinks.pick_which`, the call the Discord card makes. No row changes.
    ALTER TABLE name_actions DROP CONSTRAINT name_actions_action_check;
    ALTER TABLE name_actions ADD CONSTRAINT name_actions_action_check CHECK (action IN
        ('spell', 'kind', 'add-other', 'remove-other', 'secret', 'hide', 'merge', 'merge-many',
         'remove', 'link-add', 'link-words', 'link-secret', 'link-remove', 'link-pin',
         {WEB_WAITING_ACTIONS_LIST}));
    """
    + _which_door__3
    + r"""
    -- (a redefined function loses its pinned search path)
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_name_action_add'
            '(BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_name_action_add
        (BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, BOOLEAN) FROM PUBLIC;

    -- What waits for the DM, for the website (#2652, Supervisor's answer on #2642): the claims
    -- PlotBot found at the table, the connections a backstory found, and the "Which one?"
    -- questions, with the counts. DM only (anyone else gets NULL, as from the other doors). Counts
    -- and the DM's own words (a name, a short quote), never a transcript. A connection to a name
    -- that was refused or merged away is left out: the bot cleans the queue when it next reads it.
    CREATE OR REPLACE FUNCTION dmbot_web_waiting(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        out JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        out := jsonb_build_object(
            'status', 'ok',
            'claims', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', m.id, 'subject', m.subject, 'relationship', m.relationship,
                    'object', m.object, 'details', m.details, 'how', m.how,
                    'at', m.session_started_at)
                    ORDER BY m.created_at, m.id)
                FROM (SELECT * FROM memory_claims k
                      WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                        AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0
                        AND k.reason <> 'waiting for its name to be proposed'
                      ORDER BY k.created_at, k.id LIMIT 200) m), '[]'::jsonb),
            'backstory', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', l.id, 'subjectId', l.subject_id, 'subject', s.name,
                    'objectId', l.object_id, 'object', o.name, 'predicate', l.predicate,
                    'detail', l.detail, 'quote', l.quote,
                    'secret', l.secret OR s.audience_limited OR o.audience_limited,
                    'truth', l.truth, 'upload', l.upload,
                    'ready', s.status = 'confirmed' AND o.status = 'confirmed',
                    'waitingOn', to_jsonb(ARRAY(
                        SELECT x.name
                        FROM (VALUES (s.name, s.status), (o.name, o.status)) x(name, status)
                        WHERE x.status <> 'confirmed')))
                    ORDER BY l.created_at, l.id)
                FROM (SELECT * FROM backstory_links b
                      WHERE b.guild_id = g AND b.campaign_id = c
                      ORDER BY b.created_at, b.id LIMIT 300) l
                JOIN memory_entities s ON s.guild_id = g AND s.campaign_id = c
                                      AND s.id = l.subject_id
                                      AND s.status IN ('confirmed', 'proposed')
                JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c
                                      AND o.id = l.object_id
                                      AND o.status IN ('confirmed', 'proposed')), '[]'::jsonb),
            -- the true sizes, since the lists above are cut at their caps
            'totals', jsonb_build_object(
                'claims', (SELECT count(*) FROM memory_claims k
                           WHERE k.guild_id = g AND k.campaign_id = c AND k.status = 'proposed'
                             AND k.how <> 'plan' AND cardinality(k.speaker_ids) > 0
                             AND k.reason <> 'waiting for its name to be proposed'),
                'backstory', (SELECT count(*) FROM backstory_links b
                              WHERE b.guild_id = g AND b.campaign_id = c)),
            -- (a question left with fewer than two live candidates is not shown: nothing can be
            -- picked, and the bot deletes it when it next reads the queue)
            'which', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'id', w.id, 'word', w.word, 'otherId', w.other_id, 'other', o.name,
                    'otherIsSubject', w.other_is_subject, 'predicate', w.predicate,
                    'detail', w.detail, 'quote', w.quote, 'secret', w.secret OR o.audience_limited,
                    'upload', w.upload, 'candidates', w.cands) ORDER BY w.created_at, w.id)
                FROM (
                    SELECT q.*, (
                        SELECT jsonb_agg(jsonb_build_object('id', e.id, 'name', e.name,
                                                           'limited', e.audience_limited)
                                         ORDER BY lower(e.name), e.id)
                        FROM memory_entities e
                        WHERE e.guild_id = g AND e.campaign_id = c AND e.id = ANY (q.candidates)
                          AND e.id <> q.other_id AND e.status IN ('confirmed', 'proposed')) AS cands
                    FROM backstory_which q
                    WHERE q.guild_id = g AND q.campaign_id = c) w
                JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c
                                      AND o.id = w.other_id
                                      AND o.status IN ('confirmed', 'proposed')
                WHERE jsonb_array_length(COALESCE(w.cands, '[]'::jsonb)) >= 2), '[]'::jsonb));
        RETURN out;
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_waiting(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_waiting(BIGINT, TEXT) FROM PUBLIC;
    """
)

READING_ACTIONS = ("reading-place", "reading-no", "reading-undo")

_READING_LIST = ", ".join(f"'{a}'" for a in READING_ACTIONS)

_reading_door = _which_door__3

_reading_door__2 = _reading_door
for _old, _new in (
    (
        "'backstory-which-pick', 'backstory-which-neither')\n        THEN",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                {_READING_LIST})\n        THEN",
    ),
    (
        "'backstory-which-neither')\n               AND clean <> '')",
        f"'backstory-which-neither',\n                       {_READING_LIST})\n"
        "               AND clean <> '')",
    ),
    (
        "'backstory-which-pick', 'backstory-which-neither')\n               AND (tgt IS NULL",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND (tgt IS NULL",
    ),
    (
        "'backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(tgt, '') <> '')",
        "'backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND COALESCE(tgt, '') <> '')",
    ),
    (
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither')\n"
        "               AND COALESCE(fl, false))",
        "           OR (act IN ('backstory-which-pick', 'backstory-which-neither',\n"
        f"                       {_READING_LIST})\n"
        "               AND COALESCE(fl, false))",
    ),
    (
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' THEN",
        "        IF act LIKE 'claim-%' OR act LIKE 'backstory-%' OR act LIKE 'reading-%' THEN",
    ),
    (
        "            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (",
        "            ELSIF act IN ('reading-place', 'reading-no') AND NOT EXISTS (\n"
        "                   SELECT 1 FROM memory_claims m\n"
        "                   WHERE m.guild_id = g AND m.campaign_id = c AND m.id = tgt\n"
        "                   AND m.status = 'proposed' AND m.reading IS NOT NULL) THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act = 'reading-undo' AND NOT EXISTS (\n"
        "                   SELECT 1 FROM name_actions p\n"
        "                   WHERE p.guild_id = g AND p.campaign_id = c\n"
        "                   AND p.id = tgt AND p.user_id = dmbot_current_user()\n"
        "                   AND p.action = 'reading-place' AND p.state = 'done'\n"
        "                   AND p.result_other <> '') THEN\n"
        "                RETURN jsonb_build_object('status', 'not_found');\n"
        "            ELSIF act IN ('backstory-keep', 'backstory-skip') AND NOT EXISTS (",
    ),
):
    if _reading_door__2.count(_old) != 1:  # (an explicit raise: `python -O` drops an assert)
        raise RuntimeError(f"the name-action door changed under the readings migration: {_old!r}")
    _reading_door__2 = _reading_door__2.replace(_old, _new)


# ✏️ Change the words on the recap page's names to check (#2593; Discord's is #2570). One more answer
# for `name_answers` (`change_words`: `text` holds the whole name and `target_id` how it was heard,
# at most the name limit each), the answer door taking it, and, from the read door, the lines of
# table speech that say each name (only people who agree at that moment), for the website to work
# the likely longer names out from when the page is read: nothing is stored, so a speaker who
# stops being recorded drops out at once. Both doors are rebuilt from their current text by guarded
# replacement, so a drift in either makes this fail loudly rather than quietly change a rule.
def _swap(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    for old, new in pairs:
        if text.count(old) != 1:  # (an explicit raise: `python -O` drops an assert)
            raise RuntimeError(f"a door no longer has the text this migration changes: {old[:50]}")
        text = text.replace(old, new, 1)
    return text


_CHECK_READ_DOOR = _swap(
    NAME_GATE[
        NAME_GATE.index("    CREATE OR REPLACE FUNCTION dmbot_recap_names_read") : NAME_GATE.index(
            "    DO $do$"
        )
    ],
    (
        (
            "'note', e.description,",
            "'note', e.description, 'lines', COALESCE((\n"
            "    SELECT jsonb_agg(q.t ORDER BY q.o) FROM (\n"
            "      SELECT COALESCE(l.text, l.heard) AS t,\n"
            "             row_number() OVER (ORDER BY m.session_started_at NULLS LAST,\n"
            "                                m.created_at, m.id) AS o\n"
            "      FROM memory_mentions m\n"
            "      JOIN transcript_lines l\n"
            "        ON l.guild_id = g\n"
            "       AND l.session_id = substring(m.line_ref from '^(.*):[0-9]+:[0-9]+$')\n"
            "       AND l.started_ms::TEXT = substring(m.line_ref from ':([0-9]+):[0-9]+$')\n"
            "       AND l.user_id::TEXT = substring(m.line_ref from ':([0-9]+)$')\n"
            "      WHERE m.guild_id = g AND m.campaign_id = c AND m.entity_id = e.id\n"
            "        AND l.sidebar IS NULL\n"
            "        AND EXISTS (SELECT 1 FROM consent n\n"
            "                    WHERE n.guild_id = g AND n.user_id = l.user_id)\n"
            "      ORDER BY 2 LIMIT 8) q), '[]'::jsonb),",
        ),
    ),
)

# which items this session's report holds (the row is `m`, its answer `a`)
_REPORT_HERE = """
        ((m.session_started_at = at_
          AND NOT COALESCE(
                  a.answer IN ('intended', 'not_a_problem', 'fixed') AND a.answered_at < at_,
                  false))
         OR (a.answer = 'remind' AND m.session_started_at IS DISTINCT FROM at_
             AND at_ = (SELECT min(t.started_at) FROM transcript_sessions t
                        WHERE t.guild_id = g AND t.campaign_id = c
                          AND (t.line_count > 0 OR t.transcripts_deleted_at IS NOT NULL)
                          AND t.started_at > a.remind_from)))"""

_REPORT_KEY_C = (
    "left(encode(sha256(convert_to('contradiction|' || m.perspective_id || '|' || m.kind"
    " || '|' || m.first_relation_id || '|' || m.second_relation_id, 'UTF8')), 'hex'), 32)"
)

_REPORT_KEY_D = (
    "left(encode(sha256(convert_to('difference|' || m.perspective_id || '|' || m.relation_id,"
    " 'UTF8')), 'hex'), 32)"
)

WEB_SHEET_ACTIONS = (
    r"""
    -- A player's character-sheet choice on the website (#2816; the parity row of #2554), waiting
    -- for the bot: link a D&D Beyond sheet, forget it (the player's own), or a DM's read again of
    -- the campaign's sheets. The website never fetches a link: the bot does, with the same store
    -- calls as Discord's buttons, and checks again who plays the character, that they are still in
    -- the server, and the cooldowns. `url` is a character link of the one shape D&D Beyond uses,
    -- checked by the door before anything is queued. A row lives a day, is gone with its campaign,
    -- and is read back only by the person who made it.
    CREATE TABLE web_sheet_actions (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        -- ON UPDATE CASCADE like the tables a move rewrites (#2550).
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id          TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        seq         BIGINT GENERATED ALWAYS AS IDENTITY,
        user_id     BIGINT NOT NULL CHECK (user_id > 0),
        entity_id   TEXT NOT NULL DEFAULT '' CHECK (entity_id ~ '^([0-9a-f]{32})?$'),
        action      TEXT NOT NULL CHECK (action IN ('link', 'forget', 'read_again')),
        url         TEXT NOT NULL DEFAULT '' CHECK (char_length(url) <= 80),
        state       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (state IN ('waiting', 'working', 'done', 'failed')),
        stage       TEXT NOT NULL DEFAULT 'waiting'
            CHECK (stage IN ('waiting', 'reading', 'done', 'failed')),
        error       TEXT NOT NULL DEFAULT '' CHECK (char_length(error) <= 40),
        created_at  BIGINT NOT NULL,
        claimed_at  BIGINT NOT NULL DEFAULT 0,
        updated_at  BIGINT NOT NULL,
        PRIMARY KEY (guild_id, id),
        -- The state and the stage move together, so the page and the answer never disagree.
        CHECK ((state, stage) IN (('waiting', 'waiting'), ('working', 'reading'),
                                  ('done', 'done'), ('failed', 'failed')))
    );
    CREATE INDEX web_sheet_actions_open ON web_sheet_actions (guild_id, seq)
        WHERE state IN ('waiting', 'working');
    """
    + _isolate("web_sheet_actions")
    + r"""
    -- What the signed-in person may see of a campaign's character sheets: a DM sees every sheet
    -- (read only), a player only the sheets of the characters they play. The summary is the
    -- allow-listed snapshot's names and numbers, read here field by field. NULL for a server the
    -- person is not in, or someone who is neither a DM nor a player in this campaign.
    CREATE FUNCTION dmbot_web_sheet_read(g BIGINT, c TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        is_dm BOOLEAN;
        plays BOOLEAN;
        found_rows JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        plays := EXISTS (SELECT 1 FROM memory_entities e
                         WHERE e.guild_id = g AND e.campaign_id = c
                           AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                           AND e.status NOT IN ('merged', 'rejected'));
        IF NOT is_dm AND NOT plays THEN
            RETURN NULL;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'entityId', s.entity_id, 'name', e.name,
            'mine', e.played_by = dmbot_current_user(),
            'source', s.source, 'url', s.url, 'fetchedAt', s.fetched_at,
            'summary', CASE WHEN s.sheet IS NULL THEN NULL ELSE jsonb_build_object(
                'species', s.sheet->'species', 'level', s.sheet->'level',
                'classes', s.sheet->'classes', 'maxHp', s.sheet->'max_hp',
                'ac', s.sheet->'ac') END
        ) ORDER BY lower(e.name), s.entity_id), '[]'::jsonb) INTO found_rows
            FROM character_sheets s JOIN memory_entities e
              ON e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id AND e.id = s.entity_id
            WHERE s.guild_id = g AND s.campaign_id = c
              AND e.status NOT IN ('merged', 'rejected') AND e.played_by IS NOT NULL
              AND e.type <> 'person'
              -- Only the sheet of whoever plays the character now, never a previous player's.
              AND s.player_id = e.played_by
              AND (is_dm OR e.played_by = dmbot_current_user());
        RETURN jsonb_build_object('isDm', is_dm, 'sheets', found_rows);
    END
    $fn$;

    -- Queue one choice. `act` is link (the player's own character, a D&D Beyond character link
    -- in `link`), forget (the player's own sheet) or read_again (a DM, the campaign's sheets, once
    -- a minute). Answers 'ok' with the id; a second press of the same choice while one is open
    -- gets that one back. 'not_found' for a character or sheet that isn't there, 'not_allowed'
    -- when the caller isn't the one who plays it (or a DM, for read_again), 'bad_link' for a link
    -- of any other shape, 'too_soon' inside the link cooldown (20 seconds a person),
    -- 'read_too_soon' inside the read_again cooldown (a minute a campaign) and 'queue_full' when
    -- the queue is full. A link for another URL than an open one is queued on its own. NULL for
    -- a server the person is
    -- not in.
    CREATE FUNCTION dmbot_web_sheet_action_add(g BIGINT, c TEXT, ent TEXT, act TEXT, lnk TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        rid TEXT := replace(gen_random_uuid()::TEXT, '-', '');
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
        have TEXT;
        is_dm BOOLEAN;
        mine BOOLEAN;
        want TEXT := COALESCE(lnk, '');
        who TEXT := COALESCE(ent, '');
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        is_dm := EXISTS (SELECT 1 FROM campaign_dms d
                         WHERE d.guild_id = g AND d.campaign_id = c
                           AND d.user_id = dmbot_current_user());
        IF act IS NULL OR act NOT IN ('link', 'forget', 'read_again') THEN
            RETURN jsonb_build_object('status', 'bad_action');
        END IF;
        IF act = 'read_again' THEN
            IF NOT is_dm THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            who := '';
            want := '';
        ELSE
            IF who !~ '^[0-9a-f]{32}$' THEN
                RETURN jsonb_build_object('status', 'not_found');
            END IF;
            mine := EXISTS (SELECT 1 FROM memory_entities e
                            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = who
                              AND e.played_by = dmbot_current_user() AND e.type <> 'person'
                              AND e.status NOT IN ('merged', 'rejected'));
            IF NOT mine THEN
                RETURN jsonb_build_object('status', 'not_allowed');
            END IF;
            IF act = 'link'
               AND want !~ '^https://www[.]dndbeyond[.]com/characters/[0-9]{1,12}$' THEN
                RETURN jsonb_build_object('status', 'bad_link');
            END IF;
            IF act = 'forget' THEN
                want := '';
                IF NOT EXISTS (SELECT 1 FROM character_sheets s
                               WHERE s.guild_id = g AND s.campaign_id = c
                                 AND s.entity_id = who AND s.player_id = dmbot_current_user()) THEN
                    RETURN jsonb_build_object('status', 'not_found');
                END IF;
            END IF;
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('sheet_action:' || g::TEXT || c, 0));
        DELETE FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.created_at < now_s - 86400;
        -- A choice the bot took and then went quiet on for ten minutes goes back to waiting.
        UPDATE web_sheet_actions a SET state = 'waiting', stage = 'waiting', claimed_at = 0,
            updated_at = now_s
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state = 'working'
              AND a.updated_at < now_s - 600;
        SELECT a.id INTO have FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.entity_id = who AND a.action = act AND a.url = want
              AND a.state IN ('waiting', 'working');
        IF have IS NOT NULL THEN
            RETURN jsonb_build_object('status', 'ok', 'id', have);
        END IF;
        IF act = 'link' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.user_id = dmbot_current_user()
              AND a.action = 'link' AND a.created_at > now_s - 20) THEN
            RETURN jsonb_build_object('status', 'too_soon');
        END IF;
        IF act = 'read_again' AND EXISTS (SELECT 1 FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.action = 'read_again'
              AND a.created_at > now_s - 60) THEN
            RETURN jsonb_build_object('status', 'read_too_soon');
        END IF;
        IF (SELECT count(*) FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.state IN ('waiting', 'working')) >= 20
        THEN
            RETURN jsonb_build_object('status', 'queue_full');
        END IF;
        INSERT INTO web_sheet_actions (guild_id, campaign_id, id, user_id, entity_id, action, url,
                                       created_at, updated_at)
            VALUES (g, c, rid, dmbot_current_user(), who, act, want, now_s, now_s);
        RETURN jsonb_build_object('status', 'ok', 'id', rid);
    END
    $fn$;

    -- The state of one of the caller's own choices: the state and stage for the shared progress
    -- shape, the plain error word, and the character it was about. NULL when there is no such
    -- choice of the caller's in this campaign, or it is over a day old.
    CREATE FUNCTION dmbot_web_sheet_action_read(g BIGINT, c TEXT, aid TEXT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        r web_sheet_actions%ROWTYPE;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        SELECT a.* INTO r FROM web_sheet_actions a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.id = aid
              AND a.user_id = dmbot_current_user()
              AND a.created_at >= floor(extract(epoch FROM clock_timestamp()))::BIGINT - 86400;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('state', r.state, 'stage', r.stage, 'error', r.error,
            'action', r.action, 'entityId', r.entity_id, 'createdAt', r.created_at,
            'updatedAt', r.updated_at);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_read(BIGINT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_read(BIGINT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_add(BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_web_sheet_action_read(BIGINT, TEXT, TEXT) FROM PUBLIC;
"""
)


# "Not named yet" on the website (#2803): a DM's Names page lists the people the story described and
# never named (0263), and gives them a name through the queue the other name actions use. Three
# new actions: `name` (a new name for them: the bot renames, or answers `known_name` with the one
# DMbot already knows, or `ambiguous` / `gone`), `name-join` (the DM said "same person": the bot
# joins them into the named one, keeping it) and `name-undo` (the done one of those two, undone
# with the batch the bot kept in `result_other`). The two doors and the action list are read back
# from the database as they are now and widened, one place at a time, each of which must be found
# exactly once: a door whose text changed under this migration stops it instead of passing
# silently. A confirmed unnamed person never takes the other actions (a spelling, a kind, a secret):
# those are for named entries.
def _swaps(var: str, pairs: tuple[tuple[str, str], ...]) -> str:
    return "\n".join(
        f"        {var} := pg_temp.swap_once({var}, $o${old}$o$, $n${new}$n$);"
        for old, new in pairs
    )
