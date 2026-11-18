"""0546_document_maps

A map found inside an uploaded document points at that upload's notice (#4259, MapBot layers slice
5; Supervisor's ruling on #4259, 2026-11-18), so "↩ Undo this upload" takes its maps back.
"""

from __future__ import annotations

# `campaign_maps.from_notice`: the `upload_notices` id of the upload the map's picture came from;
# NULL for a map from chat, the website or a DM's own send. The upload's Undo removes these maps
# through `MapStore.remove_map`, so the deletion rule (§7.5) always runs: never a raw cascade. The
# key is `ON DELETE SET NULL (from_notice)`: the notice's own clean-up after its undo window must
# never delete a map the DM kept; once that window has closed the map stays as any other. It
# carries `guild_id`, so it follows a campaign moved to another server (`ON UPDATE CASCADE`, #2550),
# and the campaign, so a map can never point at another campaign's notice (the unique key on
# `upload_notices` exists for that; its index also serves the key's own look-ups).
# Adding a NULL column changes no row, so no row-level policy is opened.
SQL = r"""
    ALTER TABLE upload_notices
        ADD CONSTRAINT upload_notices_scope UNIQUE (guild_id, campaign_id, id);
    ALTER TABLE campaign_maps
        ADD COLUMN from_notice TEXT,
        ADD CONSTRAINT campaign_maps_from_notice
            FOREIGN KEY (guild_id, campaign_id, from_notice)
            REFERENCES upload_notices (guild_id, campaign_id, id)
            ON DELETE SET NULL (from_notice) ON UPDATE CASCADE;
    CREATE INDEX campaign_maps_by_notice ON campaign_maps (guild_id, campaign_id, from_notice)
        WHERE from_notice IS NOT NULL;
"""

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
