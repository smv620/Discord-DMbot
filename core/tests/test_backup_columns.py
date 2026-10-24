"""Every campaign setting is in the backup, or on a named list with a reason (#1827).

CLAUDE.md: "Backups are complete". A setting added to the `campaigns` table has to be added in
four places: the export, `_validate_backup`, `import_backup`'s write, and its new-campaign path.
A missed column means a restored campaign silently loses a setting. This reads the table's columns
from `schema.MIGRATIONS` and the backup code, with no database. (The settings tables that backups
carry beside the campaign row are sections, each with its own dump, check and load tests.)
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
import unittest
from typing import ClassVar

from dmbot import schema
from dmbot.campaigns import store
from dmbot.campaigns.store import EXPORT_FORMAT, EXPORT_VERSION, CampaignStore

# Columns of `campaigns` that are deliberately not in a backup, each with the reason (checked
# against docs/PLAN.md's backup sections). A column here that the backup also carries fails, so
# the list cannot go stale.
NOT_IN_BACKUP: dict[str, str] = {
    "id": "a restored campaign gets a new id",
    "guild_id": "a backup can be restored in another server; the restore's server is used",
    "name_key": "derived from the name, which is in the backup",
    "channel_number": "numbered afresh for the server it is restored into",
    "memory_version": "a cache counter that starts again; the memory section is what travels",
    "owner_user_id": "decided at restore: whoever restores becomes the owner (PLAN, backups)",
    "owner_left_at": "the owner's state in this server, not the campaign's setting",
    "owner_missing_since": "the owner's state in this server, not the campaign's setting",
    "paused": "a plan state the restore decides (a campaign over the plan's cap starts paused)",
    "retention_warned_stage": "the clean-up job's warning state; it starts again",
    "retention_warned_for": "the clean-up job's warning state; it starts again",
    "transcript_deleted_noted": "a note to the DM about deletions in this server; starts again",
    "house_rules_made": "carried by the house-rules section (`made`), not the campaign row",
    "dm_screen_channel_id": "a channel of one server; made again at the next /dmbot start",
    "transcript_channel_id": "a channel of one server; made again at the next /dmbot start",
    "gametime_channel_id": "a channel of one server; made again at the next /dmbot start",
    "dmbchat_channel_id": "a channel of one server; made again at the next /dmbot start",
    "dm_bot_channel_id": "a channel of one server; made again at the next /dmbot start",
    "dm_bot_message_id": "a message of one server's channel; made again with the channel",
    "rules_channel_id": "a channel of one server; made again at the next /dmbot start",
    "npcs_channel_id": "a channel of one server; made again at the next /dmbot start",
    "last_voice_channel_id": "a voice channel of one server",
    "table_chat_channel_id": "a channel of one server; only the switch (table_chat) travels",
}

# Settings the backup carries (so the file is complete) that a restore deliberately does not
# write, each with the reason. A column here that the restore does write fails, so it stays true.
NOT_RESTORED: dict[str, str] = {
    "last_played_at": "a restored campaign was last played when it was restored",
    "dndbeyond_invite": "a join link is one server's: it is in the file, a restore keeps its own",
}

TABLE_KEYWORDS = {"PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK"}


def campaign_columns(migrations: tuple[tuple[str, str], ...] = schema.MIGRATIONS) -> list[str]:
    """The `campaigns` table's columns, read from `CREATE TABLE` and `ALTER TABLE` in order."""
    columns: list[str] = []
    create = r"CREATE\s+TABLE\s+campaigns\s*\((.*?)\n\s*\)\s*;"
    added = r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)"
    for _, sql in migrations:
        text = re.sub(r"--[^\n]*", "", sql)
        for table in re.finditer(create, text, re.I | re.S):
            for line in table[1].split("\n"):
                column = re.match(r"\s*(\w+)\s+[A-Za-z]", line)
                if column and column[1].upper() not in TABLE_KEYWORDS:
                    columns.append(column[1])
        for alter in re.finditer(r"ALTER\s+TABLE\s+campaigns\b(.*?);", text, re.I | re.S):
            for add in re.finditer(added, alter[1], re.I):
                columns.append(add[1])
            for drop in re.finditer(r"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?(\w+)", alter[1], re.I):
                columns.remove(drop[1])
            for rename in re.finditer(r"RENAME\s+COLUMN\s+(\w+)\s+TO\s+(\w+)", alter[1], re.I):
                columns[columns.index(rename[1])] = rename[2]
    return columns


def exported_keys() -> set[str]:
    """The keys of the "campaign" dict that `CampaignStore.export_set` writes."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(CampaignStore.export_set)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == "campaign":
                    assert isinstance(value, ast.Dict)
                    return {str(k.value) for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("export() has no 'campaign' dict")


def validated_keys() -> set[str]:
    """The keys `_validate_backup` returns for a minimal valid backup (it is pure)."""
    backup = {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "campaign": {
            "name": "Rime",
            "created_at": 1,
            "target_ruleset": "2024",
            "fallback_ruleset": "2014",
        },
        "sections": {},
    }
    info, _ = store._validate_backup(backup, set())
    return set(info)


def imported_keys() -> set[str]:
    """The settings `import_backup` and the insert it calls read from the checked backup."""
    keys: set[str] = set()
    for function in (CampaignStore.import_backup, CampaignStore._insert):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "info"
                and isinstance(node.slice, ast.Constant)
            ):
                keys.add(str(node.slice.value))
    return keys


def problems(
    columns: list[str],
    exported: set[str],
    validated: set[str],
    imported: set[str],
    not_in_backup: dict[str, str],
    not_restored: dict[str, str],
) -> list[str]:
    out: list[str] = []
    places = (("export", exported), ("_validate_backup", validated), ("import_backup", imported))
    for column in columns:
        if column in not_in_backup:
            if column in exported:
                out.append(f"{column}: is in the export, so take it off NOT_IN_BACKUP")
            continue
        for place, keys in places:
            if column in not_restored and place == "import_backup":
                if column in keys:
                    out.append(f"{column}: is restored, so take it off NOT_RESTORED")
                continue
            if column not in keys:
                out.append(
                    f"{column}: missing from {place}; add it there, or to NOT_IN_BACKUP "
                    "(a setting a backup leaves out) or NOT_RESTORED (one a restore leaves "
                    "out) with a reason"
                )
    for listed, name in ((not_in_backup, "NOT_IN_BACKUP"), (not_restored, "NOT_RESTORED")):
        for column in listed:
            if column not in columns:
                out.append(f"{column}: on {name} but not a campaigns column any more")
    for key in sorted(exported - set(columns)):
        out.append(f"{key}: exported but not a campaigns column")
    return out


class TodaysBackupTest(unittest.TestCase):
    def test_every_campaign_column_is_in_the_backup_or_on_the_list(self) -> None:
        found = problems(
            campaign_columns(),
            exported_keys(),
            validated_keys(),
            imported_keys(),
            NOT_IN_BACKUP,
            NOT_RESTORED,
        )
        self.assertEqual(found, [])

    def test_the_readers_find_what_they_should(self) -> None:
        # (so a reader that finds nothing cannot pass for the right reason)
        columns = campaign_columns()
        self.assertGreater(len(columns), 35)
        self.assertIn("npcs_channel_id", columns)
        self.assertIn("initiative_style", exported_keys() & validated_keys() & imported_keys())
        self.assertIn("recaps_audio", validated_keys())

    def test_every_reason_is_written_out(self) -> None:
        for column, reason in {**NOT_IN_BACKUP, **NOT_RESTORED}.items():
            self.assertGreater(len(reason), 10, column)


class StandInTest(unittest.TestCase):
    columns: ClassVar[list[str]] = ["name", "story_mode", "extra"]
    full: ClassVar[set[str]] = {"name", "story_mode"}

    def check(self, **overrides: object) -> list[str]:
        args: dict[str, object] = {
            "columns": self.columns,
            "exported": self.full | {"extra"},
            "validated": self.full | {"extra"},
            "imported": self.full | {"extra"},
            "not_in_backup": {},
            "not_restored": {},
        }
        args.update(overrides)
        return problems(**args)  # type: ignore[arg-type]

    def test_a_complete_backup_passes(self) -> None:
        self.assertEqual(self.check(), [])

    def test_a_new_column_fails_until_it_is_in_the_backup_or_on_the_list(self) -> None:
        bare = {"exported": self.full, "validated": self.full, "imported": self.full}
        found = self.check(**bare)
        self.assertEqual(len(found), 3)
        self.assertTrue(all("extra" in f for f in found))
        self.assertEqual(self.check(**bare, not_in_backup={"extra": "a channel of one server"}), [])

    def test_a_column_missing_from_one_place_names_the_place(self) -> None:
        (found,) = self.check(imported=self.full)
        self.assertIn("import_backup", found)
        (found,) = self.check(validated=self.full)
        self.assertIn("_validate_backup", found)

    def test_a_listed_column_that_is_also_exported_fails(self) -> None:
        (found,) = self.check(not_in_backup={"extra": "a reason"})
        self.assertIn("take it off NOT_IN_BACKUP", found)

    def test_a_list_entry_for_a_column_that_is_gone_fails(self) -> None:
        found = self.check(not_in_backup={"gone": "a reason"})
        self.assertTrue(any("gone" in f for f in found))

    def test_a_restore_exception_is_exported_only_and_goes_stale_if_restored(self) -> None:
        self.assertEqual(self.check(imported=self.full, not_restored={"extra": "why"}), [])
        (found,) = self.check(not_restored={"extra": "why"})
        self.assertIn("take it off NOT_RESTORED", found)

    def test_columns_are_read_from_create_alter_drop_and_rename(self) -> None:
        migrations = (
            ("0001", "CREATE TABLE campaigns (\n  id TEXT PRIMARY KEY,\n  name TEXT\n);"),
            ("0002", "ALTER TABLE campaigns ADD COLUMN a INT, ADD COLUMN IF NOT EXISTS b INT;"),
            (
                "0003",
                "ALTER TABLE campaigns RENAME COLUMN a TO c; ALTER TABLE campaigns DROP COLUMN b;",
            ),
        )
        self.assertEqual(campaign_columns(migrations), ["id", "name", "c"])


if __name__ == "__main__":
    unittest.main()
