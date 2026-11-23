"""0606_web_continuity

The website's continuity heads-ups (#3695 half 1, the read door): the page shows the DM's heads-ups
as Discord's continuity card does. The bot keeps pointers (`continuity_hits`, 0605); this door
works the words out of them at read time, with consent read then. No new table.
"""

from __future__ import annotations

# `dmbot_web_continuity_read(g, c, terms, outside)` returns `{total, hits}`: the campaign's kept
# heads-ups in the table's own order (`ContinuityHits.listed`: `position`), at most 50, with the
# total, so the page can show five at a time and say "n more waiting". DM only (NULL for anyone who
# is not a DM of the campaign, a player included): a heads-up spoils the story if a player peeks.
#
# Each hit is a structure, not a sentence; the page turns it into the card's words (`line_text`):
# `{id, kind, level, name, now, earlier, ended, earlierSession, quote, pinned, learner, listed,
# choice}`. `now` is the claim as it was said (`{subject, relationship, object, details}`, the
# page words "At the table: ..."); `earlier` is the earlier fact the claim clashes with
# (`{subject, predicate, detail, object, label}`: `label` is a campaign's own kind's words, null
# for a core kind, whose words the page has), or null for the two death kinds, which read the
# entry's own open ending instead (`ended`: "dead" or "destroyed", null otherwise). `earlierSession`
# is the number of the session the earlier fact came from, by the transcripts' own numbering (as
# 0591's `since`). `quote` is the earlier claim's words, only while every one who said it is
# recorded now (`ContinuityStore._world`: `ok = bool(speakers) and all(s in opted_in ...)`);
# otherwise null and the line is still returned in DMbot's own words. `learner` and `listed` are the
# secret-clue kind's character and the names on the clue's list. `choice` names the first button as
# the card does: story_changed, they_learned_it, still_here or alive_after_all.
#
# A heads-up is left out (and not counted) when its claim is no longer waiting (answered in
# Discord since the ask), when anyone who said the claim is not recorded now (the bot's rule,
# `consent._select`, as 0604 and 0341: a consent row under the current wording, `terms`, and, while
# an outside company hears the voices, for that company, `outside`; both arrive as arguments because
# the database cannot know them), and for a death kind whose entry no longer has that ending (a
# fresh check would not raise it). The pause (off the record for the moment) lives in the bot's
# memory and a door cannot see it, the gap Supervisor accepted on #3464. Never returned: line
# numbers, speakers, the session of a line, or any transcript line. Reading changes nothing. The
# earlier fact must still be open (`to_session_at` empty), as the card's check reads it; one the DM
# has since marked secret may still show, to the DM alone, until the page asks again (it asks before
# every read).
SQL = r"""
    CREATE FUNCTION dmbot_web_continuity_read(g BIGINT, c TEXT, terms INTEGER, outside TEXT)
    RETURNS JSONB
    LANGUAGE plpgsql SECURITY DEFINER AS $fn$
    DECLARE
        agreed BIGINT[];
        total BIGINT;
        listed JSONB;
    BEGIN
        IF dmbot_current_guild() IS DISTINCT FROM g
           OR NOT (g = ANY ((SELECT dmbot_web_guilds())::BIGINT[]))
           OR NOT EXISTS (SELECT 1 FROM campaign_dms d
                          WHERE d.guild_id = g AND d.campaign_id = c
                            AND d.user_id = dmbot_current_user()) THEN
            RETURN NULL;
        END IF;
        agreed := ARRAY(SELECT n.user_id FROM consent n
                        WHERE n.guild_id = g AND n.terms_version = terms
                          AND (outside IS NULL OR n.outside_to = outside));
        WITH nums AS (
            SELECT x.started_at, row_number() OVER (ORDER BY x.started_at, x.id) AS n
            FROM transcript_sessions x
            WHERE x.guild_id = g AND x.campaign_id = c
              AND (x.line_count > 0 OR x.transcripts_deleted_at IS NOT NULL)),
        found AS (
            SELECT h.position, h.claim_id, h.kind, h.level, h.pinned, h.learner_id,
                   cl.subject AS c_subject, cl.relationship AS c_relationship,
                   cl.object AS c_object, cl.details AS c_details,
                   COALESCE(e.name, cl.subject) AS name,
                   er.id AS er_id, rs.name AS er_subject, ro.name AS er_object,
                   er.predicate AS er_predicate, er.detail AS er_detail, rp.label AS er_label,
                   es.condition AS ended, lr.name AS learner,
                   COALESCE(h.earlier_session, sd.session_started_at) AS earlier_started,
                   CASE WHEN sd.id IS NOT NULL AND cardinality(sd.speaker_ids) > 0
                             AND sd.speaker_ids <@ agreed
                        THEN concat_ws(' ', sd.subject, sd.relationship, sd.object) END AS quote,
                   CASE WHEN h.kind = 'knows_a_secret' THEN ARRAY(
                            SELECT ke.name
                            FROM memory_relations kr
                            JOIN memory_entities ke
                              ON ke.guild_id = kr.guild_id AND ke.campaign_id = kr.campaign_id
                             AND ke.id = kr.subject_id
                            WHERE kr.guild_id = g AND kr.campaign_id = c
                              AND kr.predicate = 'knows' AND kr.status = 'confirmed'
                              AND kr.to_session_at IS NULL AND kr.object_id = h.entity_id
                              AND ke.status IN ('proposed', 'confirmed') AND ke.type <> 'person'
                            ORDER BY lower(ke.name), ke.name)
                        END AS listed
            FROM continuity_hits h
            JOIN memory_claims cl
              ON cl.guild_id = h.guild_id AND cl.campaign_id = h.campaign_id
             AND cl.id = h.claim_id
            LEFT JOIN memory_entities e
              ON e.guild_id = h.guild_id AND e.campaign_id = h.campaign_id AND e.id = h.entity_id
            LEFT JOIN memory_entities lr
              ON lr.guild_id = h.guild_id AND lr.campaign_id = h.campaign_id
             AND lr.id = h.learner_id
            LEFT JOIN memory_relations er
              ON er.guild_id = h.guild_id AND er.campaign_id = h.campaign_id
             AND er.id = h.earlier_id AND er.status = 'confirmed'
             AND er.to_session_at IS NULL
            LEFT JOIN memory_entities rs
              ON rs.guild_id = er.guild_id AND rs.campaign_id = er.campaign_id
             AND rs.id = er.subject_id
            LEFT JOIN memory_entities ro
              ON ro.guild_id = er.guild_id AND ro.campaign_id = er.campaign_id
             AND ro.id = er.object_id
            LEFT JOIN memory_predicates rp
              ON rp.guild_id = er.guild_id AND rp.campaign_id = er.campaign_id
             AND rp.key = er.predicate
            LEFT JOIN LATERAL (
                SELECT s.* FROM memory_states s
                WHERE h.kind IN ('acts_after_end', 'back_from_the_end')
                  AND s.guild_id = h.guild_id AND s.campaign_id = h.campaign_id
                  AND s.entity_id = h.entity_id AND s.closed_at IS NULL
                  AND s.condition IN ('dead', 'destroyed')
                LIMIT 1) es ON TRUE
            LEFT JOIN LATERAL (
                SELECT k.* FROM memory_claims k
                WHERE k.guild_id = h.guild_id AND k.campaign_id = h.campaign_id
                  AND k.status = 'confirmed' AND k.reading IS NULL
                  AND CASE WHEN h.kind IN ('acts_after_end', 'back_from_the_end')
                           THEN k.fact_id IN (es.id, h.entity_id) ELSE k.fact_id = h.earlier_id END
                ORDER BY (k.fact_id = es.id) DESC NULLS LAST, k.created_at DESC, k.id DESC
                LIMIT 1) sd ON TRUE
            WHERE h.guild_id = g AND h.campaign_id = c
              AND cl.status = 'proposed' AND cl.reading IS NULL
              AND cardinality(cl.speaker_ids) > 0 AND cl.speaker_ids <@ agreed
              AND (h.kind NOT IN ('acts_after_end', 'back_from_the_end') OR es.id IS NOT NULL)),
        ranked AS (
            SELECT f.*, row_number() OVER (ORDER BY f.position, f.claim_id, f.kind) AS rn
            FROM found f)
        SELECT count(*),
               COALESCE(jsonb_agg(jsonb_build_object(
                   'id', r.claim_id, 'kind', r.kind, 'level', r.level, 'name', r.name,
                   'now', jsonb_build_object('subject', r.c_subject,
                                             'relationship', r.c_relationship,
                                             'object', r.c_object, 'details', r.c_details),
                   'earlier', CASE WHEN r.er_id IS NULL THEN NULL ELSE jsonb_build_object(
                                 'subject', r.er_subject, 'predicate', r.er_predicate,
                                 'detail', r.er_detail, 'object', r.er_object,
                                 'label', r.er_label) END,
                   'ended', r.ended,
                   'earlierSession', (SELECT m.n FROM nums m
                                      WHERE m.started_at = r.earlier_started),
                   'quote', r.quote, 'pinned', r.pinned, 'learner', r.learner,
                   'listed', CASE WHEN r.listed IS NULL THEN NULL ELSE to_jsonb(r.listed) END,
                   'choice', CASE WHEN r.kind = 'knows_a_secret' THEN 'they_learned_it'
                                  WHEN r.ended = 'destroyed' THEN 'still_here'
                                  WHEN r.ended IS NOT NULL THEN 'alive_after_all'
                                  ELSE 'story_changed' END)
                   ORDER BY r.rn) FILTER (WHERE r.rn <= 50), '[]'::jsonb)
            INTO total, listed
            FROM ranked r;
        RETURN jsonb_build_object('total', total, 'hits', listed);
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_continuity_read(BIGINT, TEXT, INTEGER, TEXT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_continuity_read(BIGINT, TEXT, INTEGER, TEXT) FROM PUBLIC;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("dmbot_web_continuity_read", "dmbot_web_continuity_read(BIGINT, TEXT, INTEGER, TEXT)"),
)
