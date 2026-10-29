"""0210_plot_issues

PlotBot's check of a recap and its issues (#2122).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _approve_with_versions,
    _isolate,
)


def _approve_with_plot() -> str:
    """`dmbot_recap_approve` as 0209 left it, plus the plot gate (#2122): when the website sets
    `dmbot.plot_gate` for this transaction, a text whose finished check has an issue with no
    answer is not approved ('plot_open'). It is read under the same lock as the approval, so an
    issue found between the page's read and the press cannot slip through. A check that is
    running, failed or missing never holds it (PlotBot advises; the DM decides)."""
    body = _approve_with_versions()
    anchor = "        -- While the audio is queued or being made nothing is approved over it"
    if body.count(anchor) != 1:  # (an earlier migration's text moved)
        raise RuntimeError("dmbot_recap_approve changed: the plot gate needs a new anchor")
    gate = (
        "        IF current_setting('dmbot.plot_gate', true) = 'on' AND EXISTS (\n"
        "            SELECT 1 FROM plot_checks k JOIN plot_issues i\n"
        "                ON i.guild_id = k.guild_id AND i.campaign_id = k.campaign_id\n"
        "                AND i.session_at = k.session_at AND i.text_hash = k.text_hash\n"
        "            WHERE k.guild_id = g AND k.campaign_id = c AND k.session_at = at_\n"
        "                AND k.text_hash = expected AND k.state = 'done'\n"
        "                AND i.answer_kind IS NULL) THEN\n"
        "            RETURN 'plot_open';\n"
        "        END IF;\n"
    )
    return body.replace(anchor, gate + anchor, 1)


PLOT_ISSUES = (
    """
    -- PlotBot's check of a recap against the campaign (#2122, docs/PLAN.md, "PlotBot checks every
    -- recap before it is accepted"). A check is for one text of a recap (its hash); the issues it
    -- found belong to that text and to the DM alone. New tables, so no rows change and no backfill
    -- policy; rows go with their campaign.
    -- (`plot_checks` is left out of campaign backups on purpose: it only says that a text was
    -- checked and when, and a restored campaign is checked afresh. The issues and the "not a
    -- problem" notes are backed up: they hold the DM's answers.)
    CREATE TABLE plot_checks (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        session_at  BIGINT NOT NULL,
        text_hash   TEXT NOT NULL,
        state       TEXT NOT NULL CHECK (state IN ('running', 'done', 'failed')),
        tries       INTEGER NOT NULL DEFAULT 1,
        calls       INTEGER NOT NULL DEFAULT 0,
        created_at  BIGINT NOT NULL,
        finished_at BIGINT,
        PRIMARY KEY (guild_id, campaign_id, session_at, text_hash),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE TABLE plot_issues (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        id             TEXT NOT NULL,
        session_at     BIGINT NOT NULL,
        text_hash      TEXT NOT NULL,
        kind           TEXT NOT NULL CHECK (kind IN ('contradiction', 'dead_acts', 'item_twice',
                           'place', 'timeline', 'dropped_thread', 'promise', 'secret')),
        level          TEXT NOT NULL CHECK (level IN ('possible', 'probable', 'likely')),
        sentence       INTEGER NOT NULL CHECK (sentence >= 1),
        sentence_text  TEXT NOT NULL,
        reason         TEXT NOT NULL,
        source_text    TEXT NOT NULL,
        source_session TEXT,
        fine_key       TEXT NOT NULL,
        created_at     BIGINT NOT NULL,
        answer_kind    TEXT CHECK (answer_kind IN ('fix', 'changed', 'fine', 'leave')),
        answer_note    TEXT NOT NULL DEFAULT '',
        answered_by    BIGINT,
        answered_at    BIGINT,
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    CREATE INDEX plot_issues_by_text ON plot_issues (guild_id, campaign_id, session_at, text_hash);
    -- "Not a problem": the DM's note, kept so the same issue is not raised again.
    CREATE TABLE plot_fine (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        fine_key    TEXT NOT NULL,
        note        TEXT NOT NULL,
        by_user     BIGINT NOT NULL,
        at          BIGINT NOT NULL,
        PRIMARY KEY (guild_id, campaign_id, fine_key),
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE
    );
    """
    + _isolate("plot_checks")
    + _isolate("plot_issues")
    + _isolate("plot_fine")
    + """
    -- The website reads a recap's plot check, and answers its issues, through these two doors:
    -- only a DM of the campaign, in a server on the session's list. The website's role has no
    -- right to the tables themselves. `wait_s`: how long a text may wait for its first check
    -- before the page is told the check is unavailable (the bot may be off): PlotBot advises and
    -- never holds a DM up for long.
    CREATE FUNCTION dmbot_plot_read(g BIGINT, c TEXT, at_ BIGINT, wait_s BIGINT) RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        chk plot_checks%ROWTYPE;
        h TEXT;
        st TEXT;
        issues JSONB;
        vers JSONB;
        open_n INT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_;
        IF NOT FOUND THEN
            RETURN NULL;
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        SELECT * INTO chk FROM plot_checks
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ AND text_hash = h;
        IF NOT FOUND THEN
            st := CASE WHEN now_s - job.updated_at > wait_s THEN 'unavailable' ELSE 'checking' END;
        ELSE
            st := CASE chk.state WHEN 'done' THEN 'ready' WHEN 'running' THEN 'checking'
                  ELSE 'unavailable' END;
        END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'id', i.id, 'kind', i.kind, 'level', i.level, 'sentence', i.sentence,
                   'sentenceText', i.sentence_text, 'reason', i.reason,
                   'source', jsonb_build_object('text', i.source_text,
                                                'session', i.source_session),
                   'answer', CASE WHEN i.answer_kind IS NULL THEN NULL
                                  ELSE jsonb_build_object('kind', i.answer_kind,
                                                          'note', i.answer_note) END)
                   ORDER BY i.sentence, i.id), '[]'::jsonb),
               COUNT(*) FILTER (WHERE i.answer_kind IS NULL)
            INTO issues, open_n
            FROM plot_issues i
            WHERE i.guild_id = g AND i.campaign_id = c AND i.session_at = at_
              AND i.text_hash = h AND st = 'ready';
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
                   'version', a.version, 'acceptedAt', a.accepted_at)
                   ORDER BY a.version), '[]'::jsonb)
            INTO vers FROM recap_accepted a
            WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_;
        RETURN jsonb_build_object('state', st, 'hash', h, 'issues', issues,
            'versions', vers, 'open', COALESCE(open_n, 0),
            'approvedCurrent', COALESCE((SELECT a.text_hash = h FROM recap_accepted a
                WHERE a.guild_id = g AND a.campaign_id = c AND a.session_at = at_
                ORDER BY a.version DESC LIMIT 1), false));
    END
    $fn$;
    CREATE FUNCTION dmbot_plot_answer(
        g BIGINT, c TEXT, at_ BIGINT, p_issue TEXT, p_kind TEXT, p_note TEXT) RETURNS TEXT
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        job recap_jobs%ROWTYPE;
        iss plot_issues%ROWTYPE;
        h TEXT;
        now_s BIGINT := floor(extract(epoch FROM clock_timestamp()))::BIGINT;
    BEGIN
        IF p_kind IS NULL OR p_kind NOT IN ('fix', 'changed', 'fine', 'leave')
           OR p_note IS NULL OR length(p_note) > 500 THEN
            RETURN 'bad';
        END IF;
        IF NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[])) THEN
            RETURN 'not_found';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM campaign_dms d
                       WHERE d.guild_id = g AND d.campaign_id = c
                         AND d.user_id = dmbot_current_user()) THEN
            RETURN 'not_found';
        END IF;
        SELECT * INTO job FROM recap_jobs
            WHERE guild_id = g AND campaign_id = c AND session_at = at_ FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        SELECT * INTO iss FROM plot_issues
            WHERE guild_id = g AND campaign_id = c AND id = p_issue AND session_at = at_
            FOR UPDATE;
        IF NOT FOUND THEN
            RETURN 'not_found';
        END IF;
        h := encode(sha256(convert_to(job.title || E'\\n' || job.summary, 'UTF8')), 'hex');
        IF iss.text_hash <> h THEN
            RETURN 'stale';  -- the text changed since: a new check has its own issues
        END IF;
        UPDATE plot_issues SET answer_kind = p_kind, answer_note = p_note,
            answered_by = dmbot_current_user(), answered_at = now_s
            WHERE guild_id = g AND campaign_id = c AND id = p_issue;
        IF p_kind = 'fine' THEN
            INSERT INTO plot_fine (guild_id, campaign_id, fine_key, note, by_user, at)
            VALUES (g, c, iss.fine_key, p_note, dmbot_current_user(), now_s)
            ON CONFLICT (guild_id, campaign_id, fine_key)
            DO UPDATE SET note = EXCLUDED.note, by_user = EXCLUDED.by_user, at = EXCLUDED.at;
        ELSE
            DELETE FROM plot_fine
                WHERE guild_id = g AND campaign_id = c AND fine_key = iss.fine_key;
        END IF;
        RETURN 'ok';
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
        EXECUTE format(
            'ALTER FUNCTION dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT) FROM PUBLIC;
    REVOKE ALL ON FUNCTION dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT) FROM PUBLIC;
    """
    + _approve_with_plot()
    + """
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_recap_approve(BIGINT, TEXT, BIGINT, TEXT, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp',
            current_schema());
    END
    $do$;
    """
)

SQL = PLOT_ISSUES

ISOLATED_TABLES: tuple[str, ...] = (
    "plot_checks",
    "plot_issues",
    "plot_fine",
)

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # PlotBot's check of a recap: read and answered by a DM (#2122).
    ("dmbot_plot_read", "dmbot_plot_read(BIGINT, TEXT, BIGINT, BIGINT)"),
    ("dmbot_plot_answer", "dmbot_plot_answer(BIGINT, TEXT, BIGINT, TEXT, TEXT, TEXT)"),
)
