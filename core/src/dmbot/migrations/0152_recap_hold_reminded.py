"""0152_recap_hold_reminded

The one reminder it gets (#2174).
"""

from __future__ import annotations

RECAP_HOLD_REMINDED = """
    -- The one reminder a held recap gets after a day (#2174): set when it is claimed, so the
    -- round and a restart never send it twice. A defaulted column changes no rows, so no
    -- row-level-security policy is needed.
    ALTER TABLE recap_holds ADD COLUMN reminded BOOLEAN NOT NULL DEFAULT FALSE;
"""

SQL = RECAP_HOLD_REMINDED
