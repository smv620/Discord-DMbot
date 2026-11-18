"""0541_language_question_speakers

A language question heard in a session carries who said it (#4745, a condition on
DMBOT_SESSION_LANGUAGES; Supervisor's ruling on #4744).
"""

from __future__ import annotations

# A question built from a session's lines (#4229 1d) goes with their speaker's "delete my data"
# (Stop recording me, the forget paths) and with its session's transcript, as claims, `memory_met`
# and the reveal proposals do. `speaker_ids`: who spoke the lines it was heard in (ids only, never
# words); empty for a lore upload's question, which no speaker owns. `session_id`: the session it
# was heard in, NULL for an upload's. Only a waiting question goes: a "never" is the DM's own
# answer, and stays. New columns with defaults; no row changes, so no row-level security opens.
SQL = r"""
    ALTER TABLE memory_trait_offers
        ADD COLUMN speaker_ids BIGINT[] NOT NULL DEFAULT '{}',
        ADD COLUMN session_id  TEXT CHECK (session_id IS NULL OR char_length(session_id) <= 64);
    CREATE INDEX memory_trait_offers_session ON memory_trait_offers (guild_id, session_id)
        WHERE session_id IS NOT NULL;
    """
