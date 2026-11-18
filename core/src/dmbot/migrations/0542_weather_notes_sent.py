"""0542_weather_notes_sent

Which weather DMbot has already noted for a place (#4748; Supervisor's ruling on #4747, the
conditions on DMBOT_WEATHER_NOTES).
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# One row says: DMbot has told the DMs that this kind of weather (snow, a hard frost or heat)
# doesn't fit this place's kept climate, judged against this climate row and its value. Only ids,
# a fixed word and a time, never a word of the session.
# - At most one row per place and kind (the unique key): the note comes once, not every session.
# - It comes again only when the kept climate changes: another value (`climate`), or the row it was
#   judged against is no longer among the kept ones (`memory/weather_marks.py`).
# - A merge or an Undo doesn't carry it: the note may come once more, never a leak.
# - It goes with the place, with the climate row it was judged against (a removed climate is a
#   changed one), and with the campaign; a move carries it by `ON UPDATE CASCADE`.
SQL = r"""
    CREATE TABLE memory_weather_notes (
        guild_id        BIGINT NOT NULL,
        campaign_id     TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id              TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        place_id        TEXT NOT NULL,
        kind            TEXT NOT NULL CHECK (kind IN ('snow', 'frost', 'heat')),
        climate_id      TEXT NOT NULL,
        climate         TEXT NOT NULL
            CHECK (climate IN ('arctic', 'cold', 'temperate', 'warm', 'hot')),
        created_at      BIGINT NOT NULL,
        made_by_commit  TEXT CONSTRAINT memory_weather_notes_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        UNIQUE (guild_id, campaign_id, place_id, kind),
        FOREIGN KEY (guild_id, campaign_id, place_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE,
        FOREIGN KEY (guild_id, campaign_id, climate_id)
            REFERENCES memory_map_facts (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE INDEX memory_weather_notes_by_climate
        ON memory_weather_notes (guild_id, campaign_id, climate_id);
    CREATE TRIGGER memory_weather_notes_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_weather_notes
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_weather_notes")

ISOLATED_TABLES: tuple[str, ...] = ("memory_weather_notes",)
