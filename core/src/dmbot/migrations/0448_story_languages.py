"""0448_story_languages

The languages a character knows from the story (#4229 slice 1), beside the sheets.
"""

from __future__ import annotations

from dmbot.migrations._shared import _isolate

# The owner's ruling on #4229: a character's languages live on its sheet, never in the story graph.
# An NPC has no sheet (`character_sheets` is one player's sheet, `player_id NOT NULL`, and every
# sheet reader leans on that: CloudDev-lead's call on #4229), so the languages the story gives a
# character with no sheet wait here instead, one row per language. "Who understands this?" reads
# this table and the sheets together.
#
# `name` is as the story wrote it ("Elvish", "the old tongue of Netheril"); `name_key` its plain
# lower-case form, so one language is kept once per character. `srd` is true for one of the SRD's
# languages (`not_names.LANGUAGES`); a campaign's own tongue is false. `given_by` says who gave it:
# 'read' (DMbot, from the lore or a session, until the DM checks it) or 'dm' (the DM's own word).
# Logged in the change log like every memory table, so Undo takes a change back, a merge moves the
# rows to the kept entry, and the memory backup section carries them (it holds the DM's story).
# Both foreign keys follow a campaign that moves to another server (`ON UPDATE CASCADE`, #2550) and
# go with the entry or the campaign. Only the campaign's DMs read them (the readers check). No row
# is changed, so no row-level security needs opening.
SQL = r"""
    CREATE TABLE memory_story_languages (
        guild_id       BIGINT NOT NULL,
        campaign_id    TEXT NOT NULL,
        FOREIGN KEY (campaign_id, guild_id)
            REFERENCES campaigns (id, guild_id) ON DELETE CASCADE ON UPDATE CASCADE,
        id             TEXT NOT NULL CHECK (id ~ '^[0-9a-f]{32}$'),
        entity_id      TEXT NOT NULL,
        name           TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60),
        name_key       TEXT NOT NULL CHECK (char_length(name_key) BETWEEN 1 AND 60),
        srd            BOOLEAN NOT NULL,
        given_by       TEXT NOT NULL CHECK (given_by IN ('read', 'dm')),
        created_at     BIGINT NOT NULL CHECK (created_at > 0),
        made_by_commit TEXT CONSTRAINT memory_story_languages_made_by_commit_check
            CHECK (made_by_commit ~ '^[0-9a-f]{40}$'),
        PRIMARY KEY (guild_id, campaign_id, id),
        FOREIGN KEY (guild_id, campaign_id, entity_id)
            REFERENCES memory_entities (guild_id, campaign_id, id)
            ON DELETE CASCADE ON UPDATE CASCADE
    );
    CREATE UNIQUE INDEX memory_story_languages_once
        ON memory_story_languages (guild_id, campaign_id, entity_id, name_key);
    CREATE TRIGGER memory_story_languages_stamp_commit
        BEFORE INSERT OR UPDATE ON memory_story_languages
        FOR EACH ROW EXECUTE FUNCTION dmbot_stamp_commit();
    """ + _isolate("memory_story_languages")

ISOLATED_TABLES: tuple[str, ...] = ("memory_story_languages",)
