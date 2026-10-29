"""0005_consent_terms."""

from __future__ import annotations

CONSENT_TERMS = """
    -- Which wording each person agreed to, and how (#35). Rows from before this are
    -- treated as version 1 (before "anyone in this server can read the transcript"):
    -- we can't tell which wording each saw, so they no longer count and those people
    -- are asked again.
    ALTER TABLE consent ADD COLUMN terms_version INTEGER NOT NULL DEFAULT 1
        CHECK (terms_version >= 1);
    ALTER TABLE consent ADD COLUMN method TEXT NOT NULL DEFAULT 'unknown'
        CHECK (method IN ('private_message', 'consent_command', 'unknown'));
    """

SQL = CONSENT_TERMS
