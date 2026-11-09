"""The website's notice for one upload or import (#3879): counts, the label and until when Undo
works, read through its own database door as the website's role. Only a DM of the campaign can read
it; a notice is found by its source, never "the latest"; counts and times only. Real Postgres;
invented names."""

from __future__ import annotations

from typing import Any

from dmbot.upload_notices import UNDO_SECONDS, Notice, UploadNotices
from dmbot.web import upload_notice
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session
from tests.test_recap_web import BOB
from tests.test_web_api import THURSDAY
from tests.test_web_theatre_script import Script

COUNTS = {"npc": 30, "place": 24, "connections": 48, "other_names": 3}


class UploadNoticeRead(Script):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.notices = UploadNotices(self.db)

    async def add(
        self, source_id: str = "up1", kind: str = "lore", changes: list[int] | None = None
    ) -> Notice:
        return await self.notices.add(
            THURSDAY.id,
            self.one.id,
            "The Sunken Keep",
            COUNTS,
            [1, 2, 3] if changes is None else changes,
            source_kind=kind,
            source_id=source_id,
        )

    async def read(
        self, kind: str = "lore", source_id: str = "up1", who: Session | None = None
    ) -> dict[str, Any]:
        return await upload_notice.read(self.web, who or self.session, self.cref, kind, source_id)

    async def refused(self, **kw: str) -> str:  # type: ignore[override]
        with self.assertRaises(RecapError) as caught:
            await self.read(**kw)
        return caught.exception.code

    async def test_a_notice_is_found_by_its_source_with_counts_and_the_label(self) -> None:
        made = await self.add()
        got = await self.read()
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["label"], "The Sunken Keep")
        self.assertEqual(
            got["counts"],
            {"names": 54, "byKind": {"npc": 30, "place": 24}, "connections": 48},
        )
        self.assertIsNone(got["undone"])
        # seven days from the upload, as the bot's own rule
        self.assertEqual(got["undoUntil"], made.created_at + UNDO_SECONDS)
        self.assertNotIn("changes", got)

    async def test_another_upload_or_kind_is_not_this_notice(self) -> None:
        await self.add("up1", "lore")
        none = await self.read("lore", "up2")
        self.assertEqual(none["state"], "none")
        self.assertEqual(none["counts"], {"names": 0, "byKind": {}, "connections": 0})
        self.assertIsNone(none["undone"])
        self.assertIsNone(none["undoUntil"])
        self.assertEqual((await self.read("import", "up1"))["state"], "none")

    async def test_a_notice_without_a_source_is_never_guessed_at(self) -> None:
        await self.notices.add(THURSDAY.id, self.one.id, "From Discord", COUNTS, [1])
        self.assertEqual((await self.read())["state"], "none")

    async def test_an_undone_upload_says_so_and_offers_no_undo(self) -> None:
        made = await self.add()
        assert await self.notices.claim_undo(THURSDAY.id, made.id) is not None
        got = await self.read()
        self.assertEqual(got["state"], "done")
        self.assertIsNone(got["undone"])  # (how many it took back is not kept)
        self.assertIsNone(got["undoUntil"])

    async def test_an_upload_that_changed_nothing_offers_no_undo(self) -> None:
        await self.add(changes=[])
        self.assertIsNone((await self.read())["undoUntil"])

    async def test_a_player_and_another_campaigns_dm_are_told_nothing(self) -> None:
        await self.add()
        bob = await self.signed_in(BOB)  # in the server, not a DM of the campaign
        self.assertEqual(await self._refused_as(bob), "not_found")
        with self.assertRaises(RecapError):  # a campaign this person is no DM of
            await upload_notice.read(
                self.web, self.session, f"{THURSDAY.id}.{self.two.id}", "lore", "up1"
            )

    async def _refused_as(self, who: Session) -> str:
        with self.assertRaises(RecapError) as caught:
            await self.read(who=who)
        return caught.exception.code

    async def test_a_bad_kind_or_id_is_not_found(self) -> None:
        self.assertEqual(await self.refused(kind="other"), "not_found")
        self.assertEqual(await self.refused(source_id="x y"), "not_found")
        self.assertEqual(await self.refused(source_id=""), "not_found")

    async def test_the_window_closes_after_seven_days(self) -> None:
        made = await self.add()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE upload_notices SET created_at = %s WHERE id = %s",
                (made.created_at - UNDO_SECONDS - 5, made.id),
            )
        self.assertIsNone((await self.read())["undoUntil"])

    async def test_the_same_source_id_in_another_campaign_is_not_found(self) -> None:
        await self.notices.add(
            THURSDAY.id, self.two.id, "Other", COUNTS, [1], source_kind="lore", source_id="up1"
        )
        self.assertEqual((await self.read())["state"], "none")
