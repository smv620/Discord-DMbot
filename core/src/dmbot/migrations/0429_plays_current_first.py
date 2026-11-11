"""0429_plays_current_first

A speaker's transcript label names the character they play now, before one they used to (#4137).
"""

from __future__ import annotations

# `dmbot_web_transcript_characters` (0412) labels a person at the table who has no character of
# their own role through their `plays` link (`by_plays`), one label for the whole transcript. It
# took the newest link written, whether or not it had ended: a `plays` written later and then ended
# (`to_session_at` set) beat the one that holds now. So the ones that hold come first; an ended one
# is still the fallback for a person who plays no one now. The function is read back as it is now,
# its one ordering is widened, and it is made again with every setting it had; an ordering not
# found exactly once stops the migration rather than passing silently.
SQL = r"""
    DO $$
    DECLARE
        def text;
        fresh text;
        was CONSTANT text := 'ORDER BY p.played_by, r.created_at DESC, r.id DESC';
        now_ CONSTANT text :=
            'ORDER BY p.played_by, (r.to_session_at IS NULL) DESC, r.created_at DESC, r.id DESC';
    BEGIN
        def := pg_get_functiondef(
            'dmbot_web_transcript_characters(bigint, text)'::regprocedure);
        fresh := replace(def, was, now_);
        IF length(fresh) - length(def) <> length(now_) - length(was) THEN
            RAISE EXCEPTION 'dmbot_web_transcript_characters: its plays order was not found once';
        END IF;
        EXECUTE fresh;
    END $$;
"""
