"""0249_report_read_door

A session's after-session report (#2661).
"""

from __future__ import annotations

from dmbot.migrations._shared import (
    _REPORT_HERE,
    _REPORT_KEY_C,
    _REPORT_KEY_D,
)

# One session's after-session report for the website (#2661; docs/STORY_MEMORY.md 9a): the DM's
# alone. Items are the minor contradictions and the differences PlotBot sorted as disguised,
# misunderstood or false, plus what the DM answered. An item belongs to a session when it was found
# in it (`session_started_at`); "That's intended", "Not a problem" and a fix are not raised again
# once answered before the session began, and "Remind me" comes back in the report of the session
# after the one it was answered in. The key an answer is kept under is the same hash as
# `memory/report_store.py` makes (sha256 of the item's kind and ids, 32 hex). Only ids, DMbot's own
# words (the connections' names and relationship) and numbers leave: evidence is its kind, its
# session and whether it still stands (a link whose words were removed has no target), never a
# line, a speaker or a quote. Items found but not sorted (the call limit stopped the run) are only
# counted. At most 200 of each list.
_REPORT_RELATION = """
        (SELECT jsonb_build_object(
            'id', r.id, 'subject', s.name, 'predicate', r.predicate, 'detail', r.detail,
            'directional', r.directional, 'object', o.name,
            'beliefAt', (SELECT b.session_at FROM memory_beliefs b
                         WHERE b.guild_id = g AND b.campaign_id = c
                           AND b.perspective_id = {persp} AND b.relation_id = r.id
                         ORDER BY b.seq DESC LIMIT 1),
            'evidence', COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                           'kind', e.kind, 'at', e.session_started_at,
                           'available', e.target_id IS NOT NULL) ORDER BY e.added_at, e.id)
                FROM (SELECT u.* FROM (
                          SELECT DISTINCT ON (x.kind, x.session_started_at, x.target_id) x.*
                          FROM (
                              SELECT e0.* FROM memory_evidence e0
                              WHERE e0.guild_id = g AND e0.campaign_id = c
                                AND e0.relation_id = r.id
                              UNION ALL
                              SELECT e1.* FROM memory_beliefs b2
                              JOIN memory_evidence e1
                                ON e1.guild_id = g AND e1.campaign_id = c
                               AND e1.belief_id = b2.id
                              WHERE b2.guild_id = g AND b2.campaign_id = c
                                AND b2.perspective_id = {persp} AND b2.relation_id = r.id) x
                          ORDER BY x.kind, x.session_started_at, x.target_id,
                                   x.added_at, x.id) u
                      ORDER BY u.added_at, u.id LIMIT 10) e), '[]'::jsonb))
         FROM memory_relations r
         JOIN memory_entities s ON s.guild_id = g AND s.campaign_id = c AND s.id = r.subject_id
         JOIN memory_entities o ON o.guild_id = g AND o.campaign_id = c AND o.id = r.object_id
         WHERE r.guild_id = g AND r.campaign_id = c AND r.id = {rel}
           AND (r.status = 'confirmed'
                OR (m.involves_guess AND r.status = 'proposed' AND r.secret
                    AND r.source = 'extractor')))"""

_REPORT_ANSWER = """
        CASE WHEN a.item_key IS NULL THEN NULL ELSE jsonb_build_object(
            'action', a.answer, 'at', a.answered_at, 'remindFrom', a.remind_from) END"""

_REPORT_WHO = """
        (SELECT jsonb_build_object('kind', p.kind, 'name', pe.name)
         FROM memory_perspectives p
         LEFT JOIN memory_entities pe
           ON pe.guild_id = g AND pe.campaign_id = c AND pe.id = p.entity_id
         WHERE p.guild_id = g AND p.campaign_id = c AND p.id = {persp})"""

_TRUTH = (
    "(SELECT tp.id FROM memory_perspectives tp"
    " WHERE tp.guild_id = g AND tp.campaign_id = c AND tp.kind = 'truth')"
)

REPORT_READ_DOOR = (
    r"""
    CREATE FUNCTION dmbot_web_report(g BIGINT, c TEXT, at_ BIGINT) RETURNS JSONB
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
            'now', extract(epoch FROM now())::BIGINT,
            'run', (SELECT jsonb_build_object(
                        'state', r.state, 'stage', r.stage, 'done', r.done, 'total', r.total,
                        'startedAt', r.started_at, 'updatedAt', r.updated_at, 'reason', r.reason)
                    FROM memory_report_runs r
                    WHERE r.guild_id = g AND r.campaign_id = c AND r.session_at = at_),
            'contradictions', COALESCE((
                SELECT jsonb_agg(x.item ORDER BY x.reminded DESC, x.found_at DESC, x.key)
                FROM (
                    SELECT """
    + _REPORT_KEY_C
    + r""" AS key, m.found_at,
                           COALESCE(a.answer = 'remind' AND a.remind_from < at_, false)
                               AS reminded,
                           jsonb_build_object(
                               'key', """
    + _REPORT_KEY_C
    + r""", 'kind', m.kind, 'reason', m.reason,
                               'timesUnknown', m.times_unknown,
                               'remindFrom', CASE WHEN a.answer = 'remind'
                                                       AND a.remind_from < at_
                                                  THEN a.remind_from END,
                               'who', """
    + _REPORT_WHO.format(persp="m.perspective_id")
    + r""",
                               'first', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.first_relation_id")
    + r""",
                               'second', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.second_relation_id")
    + r""",
                               'answer', """
    + _REPORT_ANSWER
    + r""") AS item
                    FROM memory_contradictions m
                    LEFT JOIN report_answers a
                      ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_C
    + r"""
                    WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'minor'
                      AND """
    + _REPORT_HERE
    + r"""
                    ORDER BY reminded DESC, m.found_at DESC, key
                    LIMIT 200) x), '[]'::jsonb),
            'differences', COALESCE((
                SELECT jsonb_agg(x.item ORDER BY x.reminded DESC, x.found_at DESC, x.key)
                FROM (
                    SELECT """
    + _REPORT_KEY_D
    + r""" AS key, m.found_at,
                           COALESCE(a.answer = 'remind' AND a.remind_from < at_, false)
                               AS reminded,
                           jsonb_build_object(
                               'key', """
    + _REPORT_KEY_D
    + r""", 'sort', m.sort, 'kind', m.kind, 'reason', m.reason,
                               'timesUnknown', m.times_unknown,
                               'remindFrom', CASE WHEN a.answer = 'remind'
                                                       AND a.remind_from < at_
                                                  THEN a.remind_from END,
                               'who', """
    + _REPORT_WHO.format(persp="m.perspective_id")
    + r""",
                               'believes', """
    + _REPORT_RELATION.format(persp="m.perspective_id", rel="m.relation_id")
    + r""",
                               'truth', CASE WHEN m.truth_relation_id IS NULL THEN NULL ELSE """
    + _REPORT_RELATION.format(persp=_TRUTH, rel="m.truth_relation_id")
    + r""" END,
                               'open', (SELECT jsonb_build_object('a', r2.subject_id,
                                                                 'b', r2.object_id)
                                        FROM memory_relations r2
                                        WHERE r2.guild_id = g AND r2.campaign_id = c
                                          AND r2.id = m.relation_id
                                          AND (r2.status = 'confirmed'
                                               OR (m.involves_guess AND r2.status = 'proposed'
                                                   AND r2.secret
                                                   AND r2.source = 'extractor'))),
                               'answer', """
    + _REPORT_ANSWER
    + r""") AS item
                    FROM memory_differences m
                    LEFT JOIN report_answers a
                      ON a.guild_id = g AND a.campaign_id = c AND a.item_key = """
    + _REPORT_KEY_D
    + r"""
                    WHERE m.guild_id = g AND m.campaign_id = c
                      AND m.sort IN ('disguised', 'misunderstood', 'false')
                      AND """
    + _REPORT_HERE
    + r"""
                    ORDER BY reminded DESC, m.found_at DESC, key
                    LIMIT 200) x), '[]'::jsonb),
            'notSorted', (
                (SELECT count(*) FROM memory_differences m
                 WHERE m.guild_id = g AND m.campaign_id = c AND m.sort = 'not_sorted'
                   AND m.session_started_at = at_)
                + (SELECT count(*) FROM memory_contradictions m
                   WHERE m.guild_id = g AND m.campaign_id = c AND m.grade = 'not_graded'
                     AND m.session_started_at = at_)));
    END
    $fn$;
    DO $do$
    BEGIN
        EXECUTE format(
            'ALTER FUNCTION dmbot_web_report(BIGINT, TEXT, BIGINT)'
            ' SET search_path = pg_catalog, %I, pg_temp', current_schema());
    END
    $do$;
    REVOKE ALL ON FUNCTION dmbot_web_report(BIGINT, TEXT, BIGINT) FROM PUBLIC;
    """
)

SQL = REPORT_READ_DOOR

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = (
    # One session's after-session report, the DM's alone (#2661).
    ("dmbot_web_report", "dmbot_web_report(BIGINT, TEXT, BIGINT)"),
)
