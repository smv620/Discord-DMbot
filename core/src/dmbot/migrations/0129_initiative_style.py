"""0129_initiative_style

The initiative style FightBot heard (#1784).
"""

from __future__ import annotations

INITIATIVE_STYLE = """
    -- How the table runs initiative, as the DM's words last set it (docs/PLAN.md, "FightBot",
    -- "Initiative style, heard"; #1784): `each`, `side`, `reroll`, then `+declare` when actions
    -- are declared first, or `declare` alone. NULL: nothing remembered. A nullable column: no
    -- row changes, so no row-level-security policy. It goes in a backup with the campaign.
    ALTER TABLE campaigns ADD COLUMN initiative_style TEXT
        CHECK (initiative_style IN (
            'each', 'side', 'reroll', 'declare',
            'each+declare', 'side+declare', 'reroll+declare'
        ));
"""

SQL = INITIATIVE_STYLE
