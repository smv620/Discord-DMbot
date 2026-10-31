"""0300_lie_statuses

Two more kinds of belief: a point of view thinks (`suspected_lie`) or knows (`known_lie`) that
someone lied (#3091, part 2).
"""

from __future__ import annotations

LIE_STATUSES = r"""
    -- A belief can now say a speaker deliberately lied, which is not the same as being mistaken:
    -- the lie is about the speaker as well as the statement. Both statuses need a `source` link
    -- (who lied), which the writer checks: a link is a row of another table, so no check here.
    -- Nothing else about beliefs changes; existing rows keep their status.
    ALTER TABLE memory_beliefs DROP CONSTRAINT memory_beliefs_status_check;
    ALTER TABLE memory_beliefs ADD CONSTRAINT memory_beliefs_status_check
        CHECK (status IN ('theory', 'accusation', 'revealed_fact', 'false_evidence',
                          'secret_plot', 'magically_verified', 'evasion_under_magic',
                          'suspected_lie', 'known_lie'));
"""

SQL = LIE_STATUSES

ISOLATED_TABLES: tuple[str, ...] = ()
