"""0457_map_facts

What DMbot knows about a place's land, climate and who rules it (#4256, MapBot layers slice 2).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# `docs/design/mapbot_layers.md` §5 and §6, with Supervisor's ruling on #4256 (2026-11-12). One row
# is one fact about one place in one layer:
# - `layer`: 'terrain', 'climate' or 'control' for now. Culture, population and route condition
#   add their own values in their slices (6 and 7); condition's `route_to` target comes with a
#   nullable `relation_id` then.
# - The value: `value` holds a word from that layer's fixed list in code (`memory/map_facts.py`) or
#   'other', with the text's own words in `words` for 'other' only. Control holds a faction entry
#   in `faction_id` instead, and no `value`. The checks below keep the two apart.
# - `how`: the value's shade, from a short list per layer (terrain: mostly or partly; climate: dry
#   or wet, Supervisor's point 1; control: rules, claims, occupies or protects). '' means none.
# - `part`: free words when it covers only part of the place ("the northern half"), '' for all.
# - Story time as connections keep it: no `from_session_at` means since before the story, and a
#   `to_session_at` closes a row as history ("fell to the orcs").
# - `status` 'read' (a reader's, waiting for the DM, shown "(DMbot read this)"), 'confirmed' (the
#   DM's) or 'rejected' (a correction, §7.3); `source` is the highest source that stated it: 'dm',
#   'map' or 'text'; `also_text` that the text stated it too (for §7.5's deletion rule).
# - `table_knows`: the players may see it (§8.3), true only for what was said at the table; the
#   DM flips it. A player sees a row only when its place and its faction are shown to them too:
#   the one reader is `MemoryStore.map_facts`.
#
# NO MAP POINTER YET (Supervisor's point 2): `campaign_maps` arrives in MapBot slice 3, which adds
# `map_id` here with its foreign key `ON DELETE SET NULL`, so §7.5's deletion rule has something to
# hold. Slice 8 is the first to write it. Nothing in this slice comes from a map.
#
# One open row per place, layer, value and part (a rejected or closed row is history). Per campaign
# with forced row-level security; every foreign key follows a campaign moved to another server
# (`ON UPDATE CASCADE`, #2550) and goes with its place, faction or campaign (`ON DELETE CASCADE`).
# Logged in the change log like every memory table, so Undo takes a change back, a merge moves the
# rows and the memory backup section carries them (they can't be rebuilt from anything else). No
# existing row changes, so no row-level security needs opening.
SQL = r"""
    CREATE TABLE memory_map_facts (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        entity_id       TEXT NOT NULL,
        layer           TEXT NOT NULL CHECK (layer IN ('terrain', 'climate', 'control')),
        value           TEXT NOT NULL DEFAULT '' CHECK (value ~ '^[a-z]{0,20}$'),
        words           TEXT NOT NULL DEFAULT '' CHECK (char_length(words) <= 60),
        faction_id      TEXT,
        how             TEXT NOT NULL DEFAULT '' CHECK (how ~ '^[a-z]{0,20}$'),
        part            TEXT NOT NULL DEFAULT '' CHECK (char_length(part) <= 60),
        from_session_at BIGINT,
        to_session_at   BIGINT,
        from_game_time  BIGINT,
        to_game_time    BIGINT,
        status          TEXT NOT NULL CHECK (status IN ('read', 'confirmed', 'rejected')),
        source          TEXT NOT NULL CHECK (source IN ('dm', 'map', 'text')),
        also_text       BOOLEAN NOT NULL DEFAULT false,
        table_knows     BOOLEAN NOT NULL DEFAULT false,
        created_at      BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit  TEXT CONSTRAINT memory_map_facts_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, faction_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        -- control names a faction and no word; the others a word and no faction
        CONSTRAINT memory_map_facts_value_kind CHECK (
            (layer = 'control') = (faction_id IS NOT NULL)
            AND (layer = 'control') = (value = '')
        ),
        -- the text's own words only with 'other'
        CONSTRAINT memory_map_facts_other_words CHECK ((value = 'other') = (words <> '')),
        CONSTRAINT memory_map_facts_time_order CHECK (
            to_session_at IS NULL OR from_session_at IS NULL OR to_session_at >= from_session_at
        )
    );
    -- one open row per place, layer, value and part (the faction is control's value)
    CREATE UNIQUE INDEX memory_map_facts_open
        ON memory_map_facts (guild_id, campaign_id, entity_id, layer, value,
                             COALESCE(faction_id, ''), lower(part))
        WHERE status <> 'rejected' AND to_session_at IS NULL;
    CREATE INDEX memory_map_facts_faction
        ON memory_map_facts (guild_id, campaign_id, faction_id) WHERE faction_id IS NOT NULL;
    CREATE TRIGGER memory_map_facts_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_map_facts
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_map_facts")

ISOLATED_TABLES: tuple[str, ...] = ("memory_map_facts",)
