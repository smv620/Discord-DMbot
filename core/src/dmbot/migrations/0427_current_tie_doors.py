"""0427_current_tie_doors

Three doors that describe someone as they are now read current connections only (#4134).
"""

from __future__ import annotations

# Found by #4115's audit of every reader of `memory_relations`: a connection ends by setting
# `to_session_at` (the history is kept), and these three read an ended one as if it still held.
# - `dmbot_web_which_info` (0342): the ties behind the website's "Which one?" tags ("the woman
#   (in Termalaine)"), which must never name a town she left;
# - `dmbot_web_who_answers` (0371): "a.k.a. the woman, with X", the oldest tie, which could be
#   one that ended;
# - `dmbot_name_group_info` (0335): the groups a name's look-alikes are members of, for the "may
#   be group G" hint, which must not count a group they left.
# Each is read back as it is now, the one filter it has on what is kept is widened, and it is made
# again with every setting it had. A filter not found exactly as many times as expected stops the
# migration rather than passing silently. Readers that mean history on purpose (the as-of reader,
# who-knows, merges, Undo, backups, the same-person evidence: a guard who once worked the gate is
# still evidence of who he is) stay as they are, and so does the order of the name dropdown (only
# an order: a place someone left still makes them likelier to be meant). A speaker's `plays` in a
# transcript (0412) is one label for the whole transcript and keeps an ended one as a fallback;
# putting current ones first there is #4134's follow-up.
SQL = r"""
    DO $$
    DECLARE
        def text;
        fresh text;
        added CONSTANT text := ' AND r.to_session_at IS NULL';
        added_z CONSTANT text := ' AND z.to_session_at IS NULL';
    BEGIN
        def := pg_get_functiondef('dmbot_web_which_info(bigint, text, text[])'::regprocedure);
        fresh := replace(def, 'z.campaign_id = c AND z.status = ''confirmed''',
                         'z.campaign_id = c AND z.status = ''confirmed''' || added_z);
        IF length(fresh) - length(def) <> length(added_z) THEN
            RAISE EXCEPTION 'dmbot_web_which_info: its tie filter was not found exactly once';
        END IF;
        EXECUTE fresh;

        def := pg_get_functiondef('dmbot_web_who_answers(bigint, text, text[])'::regprocedure);
        fresh := replace(def, 'AND NOT r.secret AND t.id IN (r.subject_id, r.object_id)',
                         'AND NOT r.secret AND t.id IN (r.subject_id, r.object_id)' || added);
        IF length(fresh) - length(def) <> length(added) THEN
            RAISE EXCEPTION 'dmbot_web_who_answers: its tie filter was not found exactly once';
        END IF;
        EXECUTE fresh;

        def := pg_get_functiondef('dmbot_name_group_info(bigint, text, text[])'::regprocedure);
        fresh := regexp_replace(
            def, 'r\.status = ''confirmed''(\s+)AND NOT r\.secret AND NOT r\.unrevealed',
            'r.status = ''confirmed''\1AND NOT r.secret AND NOT r.unrevealed' || added, 'g');
        IF length(fresh) - length(def) <> 2 * length(added) THEN
            RAISE EXCEPTION 'dmbot_name_group_info: its two member filters were not found';
        END IF;
        EXECUTE fresh;
    END $$;
"""
