"""0269_report_fixed_remind

Undo of a fix gives a reminder back (#2661).
"""

from __future__ import annotations

REPORT_FIXED_REMIND = """
    -- A `fixed` answer (#2661) replaces a "remind me": the date goes here, so Undoing the change
    -- that fixed the item puts the reminder back as it was.
    ALTER TABLE report_answers ADD COLUMN remind_was BIGINT;
    ALTER TABLE report_answers ADD CONSTRAINT report_answers_remind_was
        CHECK (remind_was IS NULL OR answer = 'fixed');
"""

SQL = REPORT_FIXED_REMIND
