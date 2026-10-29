"""0036_transcript_sidebar."""

from __future__ import annotations

TRANSCRIPT_SIDEBAR = """
    -- DM sidebar lines (#935): the DM's question and DMbot's answer, NULL for table speech.
    -- In the as-heard (raw) transcript for everyone who may read transcripts, never in the
    -- cleaned one (owner, #933). Every one records where it came from: a question its short
    -- id, how it came in and the speech-to-text used; a reply the question it answers, the
    -- AI model, the prompt version and the sources it used.
    ALTER TABLE transcript_lines
        ADD COLUMN sidebar TEXT CHECK (sidebar IN ('question', 'answer')),
        ADD COLUMN sidebar_ref TEXT,
        ADD COLUMN sidebar_reply_to TEXT,
        ADD COLUMN sidebar_via TEXT CHECK (sidebar_via IN ('voice-memo', 'typed', 'table-trigger')),
        ADD COLUMN sidebar_stt TEXT,
        ADD COLUMN sidebar_model TEXT,
        ADD COLUMN sidebar_prompt TEXT,
        ADD COLUMN sidebar_sources JSONB,
        ADD CONSTRAINT transcript_lines_sidebar_lineage
            CHECK (sidebar IS NOT NULL OR (sidebar_ref IS NULL AND sidebar_reply_to IS NULL
                AND sidebar_via IS NULL AND sidebar_stt IS NULL AND sidebar_model IS NULL
                AND sidebar_prompt IS NULL AND sidebar_sources IS NULL));
    """

SQL = TRANSCRIPT_SIDEBAR
