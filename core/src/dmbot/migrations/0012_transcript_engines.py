"""0012_transcript_engines."""

from __future__ import annotations

TRANSCRIPT_ENGINES = """
    -- Which speech-to-text wrote each session down (#173): "engine model host", one
    -- per engine used (a session resumed after a restart may switch). Not secret: no
    -- keys, only the engine, its model and the endpoint's host.
    ALTER TABLE transcript_sessions ADD COLUMN engines TEXT[] NOT NULL DEFAULT '{}';
    """

SQL = TRANSCRIPT_ENGINES
