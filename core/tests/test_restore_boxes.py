"""The restore command has a box for every file a copy can be made of (#2606), and a campaign that
needs more files than that is not copied, with the reason said plainly. Needs Postgres."""

from __future__ import annotations

from unittest.mock import patch

from dmbot.campaigns import parts as pt
from dmbot.campaigns import store as store_module
from dmbot.campaigns.store import BackupTooBig
from dmbot.ui.dmbot_commands import dmbot_restore
from tests.test_transcript_parts import GUILD_A, _Base


class Boxes(_Base):
    def test_there_is_a_box_for_every_file_a_copy_can_have_and_the_command_stays_within_25(
        self,
    ) -> None:
        names = [p.name for p in dmbot_restore.parameters]
        self.assertEqual(names[0], "file")
        self.assertEqual(names[1:], [f"transcripts_{n}" for n in range(1, pt.RESTORE_BOXES + 1)])
        self.assertLessEqual(len(names), 25)  # (Discord's limit on a command's boxes)

    async def test_a_campaign_that_needs_more_files_than_there_are_boxes_is_not_copied(
        self,
    ) -> None:
        for n in range(3):
            await self.session(1000 + n * 1000, 3)
        async with self.db.guild(GUILD_A) as conn:
            sizes = [len(t) async for t in pt.TranscriptParts().sessions(conn, GUILD_A, self.cid)]
        # a part holds one session each, and two boxes are all a restore would have
        with (
            patch.object(store_module, "PART_TEXT_BYTES", max(sizes) + 10),
            patch.object(store_module, "RESTORE_BOXES", 2),
            self.assertRaises(BackupTooBig) as raised,
        ):
            await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertIn("more than", str(raised.exception))
        self.assertIn("Nothing was lost", str(raised.exception))

    async def test_a_campaign_that_fits_the_boxes_is_copied(self) -> None:
        for n in range(3):
            await self.session(1000 + n * 1000, 3)
        async with self.db.guild(GUILD_A) as conn:
            sizes = [len(t) async for t in pt.TranscriptParts().sessions(conn, GUILD_A, self.cid)]
        with (
            patch.object(store_module, "PART_TEXT_BYTES", max(sizes) + 10),
            patch.object(store_module, "RESTORE_BOXES", 3),
        ):
            _, files = await self.campaigns.export_set(GUILD_A, self.cid)
        self.assertEqual(len(files), 3)
