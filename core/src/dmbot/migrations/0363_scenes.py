"""0363_scenes

The scene of a question: a short AI summary of who, where and what (#3596).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

SCENES = r"""
    -- The scene of a question about an entry or a claim (#3596, PLAN card part 4a): one to three
    -- plain sentences the AI wrote from the lines the question came from, so the DM can tell which
    -- "someone (name unknown)" it was. DM-only everywhere: no player door reads this table. The
    -- text is the AI's telling, never a transcript line. `speaker_ids` are the people whose lines
    -- it was made from: the scene goes when any of them stops being recorded (an entry the lore or
    -- a backstory made has none: the DM's own document is not a speaker's consented speech).
    -- It is kept only while its question is open; a sweep deletes it once the entry is named,
    -- joined, removed or decided, or the claim is answered, so there is no foreign key to the
    -- subject (an entry or a claim, by id).
    CREATE TABLE memory_scenes (
        guild_id           BIGINT NOT NULL,
        campaign_id        TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        subject_kind       TEXT NOT NULL CHECK (subject_kind IN ('entity', 'claim')),
        subject_id         TEXT NOT NULL CHECK (subject_id ~ '^[0-9a-f]{32}$'),
        scene              TEXT NOT NULL CHECK (char_length(scene) BETWEEN 1 AND 600),
        lines_n            INTEGER NOT NULL CHECK (lines_n >= 0),
        speaker_ids        BIGINT[] NOT NULL,
        session_started_at BIGINT,
        prompt_version     TEXT NOT NULL CHECK (char_length(prompt_version) BETWEEN 1 AND 40),
        model              TEXT NOT NULL CHECK (char_length(model) <= 80),
        created_at         BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, subject_kind, subject_id)
    );

    -- The scenes of these questions, for the website (WebDev-lead attaches them to the Names
    -- page's "Not named yet" editor and the Questions page). Only a DM of the campaign is given
    -- anything: anyone else, a member who is not a DM included, gets NULL, as does anyone who is
    -- not a member of the server. Nothing is written, and at most 200 ids are read.
    CREATE FUNCTION dmbot_web_scenes(g BIGINT, c TEXT, kind TEXT, ids TEXT[]) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaigns WHERE guild_id = g AND id = c) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        RETURN jsonb_build_object('status', 'ok', 'scenes', COALESCE((
            SELECT jsonb_object_agg(s.subject_id, s.scene)
            FROM (SELECT x.subject_id, x.scene FROM memory_scenes x
                  WHERE x.guild_id = g AND x.campaign_id = c AND x.subject_kind = kind
                    AND x.subject_id = ANY (ids[1:200])
                    -- only while its question is open: an answered one may not be swept yet
                    AND ((x.subject_kind = 'entity' AND EXISTS (
                            SELECT 1 FROM memory_entities e
                            WHERE e.guild_id = g AND e.campaign_id = c AND e.id = x.subject_id
                              AND e.merged_into IS NULL
                              AND (e.status = 'proposed'
                                   OR (e.status = 'confirmed' AND e.unnamed))))
                      OR (x.subject_kind = 'claim' AND EXISTS (
                            SELECT 1 FROM memory_claims q
                            WHERE q.guild_id = g AND q.campaign_id = c AND q.id = x.subject_id
                              AND q.status = 'proposed')))
                  ORDER BY x.subject_id LIMIT 200) s), '{}'::jsonb));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_scenes(BIGINT, TEXT, TEXT, TEXT[])'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_scenes(BIGINT, TEXT, TEXT, TEXT[]) FROM PUBLIC;
    """ + _isolate("memory_scenes")

SQL = SCENES

ISOLATED_TABLES: tuple[str, ...] = ("memory_scenes",)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_scenes", "dmbot_web_scenes(BIGINT, TEXT, TEXT, TEXT[])"),
)
