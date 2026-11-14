"""0474_story_chain_town

The party's last town is kept from session to session (#4338, a follow-up to #4173 slice 6d part 3).
"""

from __future__ import annotations

# A session that ends with the party on the road ("three days on the road") has no town left on its
# chain: travel closed it. The walk remembered the town it set out from only for that one walk, so
# the next session's first arrival wrote no journey. One more row kind, 'town', holds it: rank 0,
# the place's id, beside the 'mark' row. It is the party's (the main chain's) only; a split group's
# walk carries it on as it is. Ids only, as every other row. A place deleted takes the row with it
# (the foreign key cascades, as the chain rows); a place merged away or turned down is left out when
# the chains are read, as theirs are. No existing row changes, so no row-level security needs
# opening.
SQL = r"""
    ALTER TABLE memory_story_chain DROP CONSTRAINT memory_story_chain_chain_check;
    ALTER TABLE memory_story_chain ADD CONSTRAINT memory_story_chain_chain_check
        CHECK (chain ~ '^(main|mark|town|s[0-9]{1,12}g[1-3])$');
    ALTER TABLE memory_story_chain ADD CONSTRAINT memory_story_chain_town
        CHECK (chain <> 'town' OR rank = 0);
    """

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
