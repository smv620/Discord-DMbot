"""0459_setting_from

Who said a place is the campaign's setting: DMbot, from the adventure, or the DM (#4173 slice 2b).
"""

from __future__ import annotations

# Supervisor's ruling on #4173 (2026-11-12 22:20): a setting DMbot worked out from the shared
# adventure (`memory/setting_worlds.py`) is a guess until the DM keeps it, shown as one, and places
# count as inside it only once kept. `setting_from` says which:
# - 'read': DMbot's guess (`setting` true);
# - 'dm': the DM's own word (`setting` true), every setting made before this migration included;
# - 'declined': the DM said ✖ Not the setting to a guess (`setting` false), so DMbot never guesses
#   again for this campaign (the ux-critic on #4173);
# - NULL: nothing said (`setting` false).
# The check keeps the two in step. Carried by the change log, merges and backups like `setting`.
#
# The website, level with Discord (the reviewer on #4173): the All names door
# (`dmbot_web_all_names`) sends `setting` true to a player only for a setting the DM has kept, and
# sends the DM `settingFrom` as well, so the site can mark a guess. The door is read back from the
# database as it is and its anchor (0456's line) must be found exactly once, so a drift stops the
# migration (as 0437, 0442 and 0456 do).
#
# The backfill changes rows of a table with forced row-level security, so it opens a policy for
# itself and closes it after (CLAUDE.md, the lesson of #704); it reads only memory_entities.
_ALL_NAMES_SWAPS = (
    (
        "                   e.form, e.form_from,\n",
        "                   e.form, e.form_from, e.setting_from,\n",
        1,
    ),
    (
        "                    'setting', p.setting, 'form', p.form, 'formFrom', p.form_from,\n",
        "                    'setting', p.setting AND (is_dm OR p.setting_from = 'dm'),\n"
        "                    'settingFrom', CASE WHEN is_dm THEN p.setting_from END,\n"
        "                    'form', p.form, 'formFrom', p.form_from,\n",
        1,
    ),
)


def _dollar(text: str) -> str:
    return "$q$" + text + "$q$"


def _rebuild(door: str, swaps: tuple[tuple[str, str, int], ...]) -> str:
    steps = "".join(
        f"\n        def := pg_temp.swap_n(def, {_dollar(old)}, {_dollar(new)}, {n});"
        for old, new, n in swaps
    )
    return (
        r"""
    DO $do$
    DECLARE
        def text;
    BEGIN
        SELECT pg_get_functiondef(p.oid) INTO def FROM pg_proc p
            WHERE p.pronamespace = current_schema()::regnamespace
              AND p.proname = '"""
        + door
        + "';"
        + steps
        + r"""
        EXECUTE def;
    END
    $do$;
    """
    )


SQL = r"""
    ALTER TABLE memory_entities ADD COLUMN setting_from TEXT
        CHECK (setting_from IN ('read', 'dm', 'declined'));
    CREATE POLICY migrate_backfill ON memory_entities FOR ALL USING (true) WITH CHECK (true);
    UPDATE memory_entities SET setting_from = 'dm' WHERE setting;
    DROP POLICY migrate_backfill ON memory_entities;
    ALTER TABLE memory_entities ADD CONSTRAINT memory_entities_setting_from_pair
        CHECK (setting = (setting_from IS NOT NULL AND setting_from <> 'declined'));

    CREATE OR REPLACE FUNCTION pg_temp.swap_n(def text, old text, new text, n integer) RETURNS text
    LANGUAGE plpgsql AS $f$
    BEGIN
        IF (length(def) - length(replace(def, old, ''))) <> length(old) * n THEN
            RAISE EXCEPTION 'not found % times: %', n, left(old, 80);
        END IF;
        RETURN replace(def, old, new);
    END
    $f$;
    """ + _rebuild("dmbot_web_all_names", _ALL_NAMES_SWAPS)

ISOLATED_TABLES: tuple[str, ...] = ()

WEB_ROLE_FUNCTIONS: tuple[tuple[str, str], ...] = ()
