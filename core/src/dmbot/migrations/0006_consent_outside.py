"""0006_consent_outside."""

from __future__ import annotations

CONSENT_OUTSIDE = """
    -- Which outside speech-to-text engine (deepgram, cloud) the consent request named
    -- when this yes was given; NULL = it named none (local Whisper). While the server
    -- uses an outside engine, only yeses for that same engine count; everyone else is
    -- asked again (#170, docs/PLAN.md "Consent"). Existing yeses: NULL.
    ALTER TABLE consent ADD COLUMN IF NOT EXISTS outside_to TEXT;
    """

SQL = CONSENT_OUTSIDE
