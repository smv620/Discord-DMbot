"""0414_met_in_play_reason

A new reason for a rank, `met_in_play` (#3172 slice 5): a being the party met in play that the
story never named, kept quietly at rank 0 ("anyone or anything met; kept so they can be raised
later", the owner's level 0). The reason is what marks such an entry as quiet: it stays out of the
name check and of "Add all" until the DM gives it a name or the story raises it.
"""

from __future__ import annotations

# Only the CHECK changes (it names the reasons `importance_rank.REASONS` lists; a test keeps the
# two equal). No row changes, so no policy is opened.
SQL = r"""
    ALTER TABLE memory_importance DROP CONSTRAINT memory_importance_reason_check;
    ALTER TABLE memory_importance ADD CONSTRAINT memory_importance_reason_check
        CHECK (reason IN ('reading', 'dm_corrected', 'revealed', 'demoted', 'rule', 'met_in_play'));
"""
