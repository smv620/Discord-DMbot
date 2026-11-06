"""0357_kind_offers

The answers a DM gave to "make these words a kind of connection?" (#3033).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

KIND_OFFERS = r"""
    -- DMbot offers to turn the words a table keeps using for a connection ("sworn on the ashes of",
    -- said 3 times) into a kind of its own (#3033). The DM answers Yes (a kind is made; nothing is
    -- kept here), Not now ('later': asked again only once the count has grown by 3 from `seen`) or
    -- Never ('never': final for these words in this campaign until the DM clears it). `words_key`
    -- is the folded words exactly as they already sit on the campaign's catch-all connections,
    -- never any other text. A row whose words no longer appear on a live catch-all connection is
    -- deleted by the sweep that counts, so nothing is kept for words the campaign no longer has.
    -- Carried in backups (`memory/kind_offers_backup.py`) and moves with its campaign.
    CREATE TABLE memory_kind_offers (
        guild_id    BIGINT NOT NULL,
        campaign_id TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        words_key   TEXT NOT NULL CHECK (char_length(words_key) BETWEEN 1 AND 300),
        state       TEXT NOT NULL CHECK (state IN ('later', 'never')),
        seen        INTEGER NOT NULL CHECK (seen >= 0),
        created_at  BIGINT NOT NULL CHECK (created_at > 0),
        PRIMARY KEY (guild_id, campaign_id, words_key)
    );
    """ + _isolate("memory_kind_offers")

SQL = KIND_OFFERS

ISOLATED_TABLES: tuple[str, ...] = ("memory_kind_offers",)
