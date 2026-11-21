"""0574_goodbye_question_speakers

A goodbye question heard in a session carries who said it and the session (#4537, party knowledge
slice 6b; Supervisor's ruling T on slice 6, 2026-11-21).
"""

from __future__ import annotations

# A `leave` question (0570) is built from a session's lines: the DM's, or the leaving character's
# own player's. So it goes with their speaker's "delete my data" (Stop recording me, the forget
# paths) and with its session's transcript, as the language questions do (0541, #4745).
# `speaker_ids`: who spoke the lines the goodbye was heard in (ids only, never words); empty for a
# join, a coming back or a death, which no speaker owns. `session_id`: the session it was heard
# in, NULL for the others; it also keeps one goodbye asked once per session, so a re-read of the
# same session never asks again after "Still in the party". Only a waiting question goes: an
# answer is the DM's own, and stays. New columns with defaults; no row changes, so no row-level
# security opens.
SQL = r"""
    ALTER TABLE memory_join_questions
        ADD COLUMN speaker_ids BIGINT[] NOT NULL DEFAULT '{}',
        ADD COLUMN session_id  TEXT CHECK (session_id IS NULL OR char_length(session_id) <= 64);
    CREATE INDEX memory_join_questions_session ON memory_join_questions (guild_id, session_id)
        WHERE session_id IS NOT NULL;
    """
