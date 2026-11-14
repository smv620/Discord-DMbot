"""The map close-read sweep (#4349, MapBot 6b-2; Supervisor's ruling on #4349): a kept map is read
closely once, after both yeses, every check asked again; its mark is claimed in the same step that
checks it, so two sweeps never read one map; a failed read is tried again at most 3 times; a read
put off (no AI credits, the day's limit) costs no try; the read goes quietly into the map's note.
The real stores on a test database; the bot, the bucket and the AI are stand-ins. Invented ids and
places. Needs Postgres."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from dmbot import images
from dmbot.ai import AIOutOfFunds
from dmbot.bot import DMBot
from dmbot.campaigns import CampaignStore
from dmbot.image_desk import DayLimit
from dmbot.map_notes import MapNotesSection, MapNoteStore
from dmbot.maps.store import CLOSE_READ_LEASE_S, CLOSE_READ_TRIES, MapStore, Viewer
from dmbot.memory.store import MemoryStore
from dmbot.pictures.store import MB, PictureStore
from tests.pg import DatabaseTest
from tests.test_map_question import DM, GUILD, PLAYER

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
CLOSE = json.dumps({
    "level": "region",
    "places": [{"name": "Harrowmere", "where": "on the coast", "level": "settlement",
                "form": "land"},
               {"name": "Grey Sound", "where": "west", "level": "", "form": "water"}],
    "links": [{"a": "Harrowmere", "kind": "next_to", "b": "Grey Sound",
               "word": "on the coast of", "sure": True}],
    "layout": "A road runs east.",
})  # fmt: skip


class Sweep(DatabaseTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.campaigns = CampaignStore(self.db)
        self.campaigns.register_section(MapNotesSection())
        self.pictures = PictureStore(self.db, clock=lambda: 1000.0)
        self.memory = MemoryStore(self.db)
        self.maps = MapStore(self.db, self.memory)
        self.campaign = await self.campaigns.create(GUILD, "One", DM)
        self.consenting = True
        self.bot = SimpleNamespace(
            picture_store=self.pictures,
            map_store=self.maps,
            campaigns=self.campaigns,
            consent=SimpleNamespace(has_image_consent=lambda guild, user: self.consenting),
        )
        self.now = 2000.0
        self.maps._clock = lambda: self.now
        self.notes = MapNoteStore(self.db)
        self.read_closely = AsyncMock(return_value=images.parse_close_map(CLOSE))
        self.allowed = True
        self.bot.map_notes = self.notes
        self.bot.image_desk = SimpleNamespace(read_closely=self.read_closely)
        self.bot.picture_bucket = SimpleNamespace(get=AsyncMock(return_value=PNG))
        self.bot.plan_allows = AsyncMock(side_effect=lambda *a, **k: self.allowed)
        self.bot._maps_may_be_kept = AsyncMock(return_value=True)

    async def picture(self, poster: int, n: int = 1, message_id: int = 11) -> int:
        added = await self.pictures.add(
            GUILD, self.campaign.id, poster_id=poster, session_id="", line_id=None,
            message_id=message_id, description="", consent_version=7, right_granted_at=900,
            sha256=f"{n:064x}", object_key=f"p/{n}.png", preview_key="", size_bytes=MB,
            preview_bytes=0, storage_mb=5,
        )  # fmt: skip
        assert added.picture is not None
        return added.picture.id

    async def kept(self, poster: int, n: int = 1, message_id: int = 11) -> Any:
        pic = await self.picture(poster, n, message_id)
        await self.maps.add_map(
            GUILD, self.campaign.id, picture_id=pic, uploader_id=poster,
            uploader_is_dm=poster == DM, right_to_use=True, kind="other", came_from="discord",
            audience="table", storage_mb=5,
        )  # fmt: skip
        return next(m for m in await self.maps.maps(GUILD, self.campaign.id, Viewer(dm=True))
                    if m.picture_id == pic)  # fmt: skip

    async def sweep_one(self, a_map: Any) -> str:
        return await DMBot._close_read_one(self.bot, GUILD, self.campaign.id, a_map)  # type: ignore[arg-type]

    async def waiting_ids(self) -> list[str]:
        return [m.id for _, m in await self.maps.close_read_waiting(GUILD)]

    async def test_a_dms_map_is_read_once_into_its_note(self) -> None:
        a_map = await self.kept(DM)
        self.assertEqual(await self.waiting_ids(), [a_map.id])
        self.assertEqual(await self.sweep_one(a_map), "read")
        [note] = await self.notes.notes(GUILD, self.campaign.id)
        assert note.close is not None
        self.assertEqual((note.close["level"], note.user_id), ("region", DM))
        self.assertEqual(await self.waiting_ids(), [])  # (read: never again)

    async def test_the_read_goes_onto_the_pictures_existing_note(self) -> None:
        a_map = await self.kept(DM)
        plain = images.Read(images.MAP, "A map.", "", (images.Place("Harrowmere", "east"),), "")
        await self.notes.add(GUILD, self.campaign.id, DM, plain, 11)
        await self.sweep_one(a_map)
        [note] = await self.notes.notes(GUILD, self.campaign.id)  # (one note, now with its read)
        self.assertIsNotNone(note.close)

    async def test_a_players_map_waits_for_a_dms_yes(self) -> None:
        a_map = await self.kept(PLAYER)
        self.assertEqual(await self.waiting_ids(), [])
        await self.maps.answer_use(GUILD, self.campaign.id, a_map.id, dm_id=DM, yes=True)
        self.assertEqual(await self.waiting_ids(), [a_map.id])

    async def test_two_sweeps_never_read_one_map(self) -> None:
        a_map = await self.kept(DM)
        self.assertTrue(await self.maps.claim_close_read(GUILD, self.campaign.id, a_map.id))
        self.assertFalse(await self.maps.claim_close_read(GUILD, self.campaign.id, a_map.id))
        self.assertEqual(await self.sweep_one(a_map), "taken")
        self.read_closely.assert_not_awaited()
        self.now += CLOSE_READ_LEASE_S + 1  # (a sweep that stopped mid-read: taken again)
        self.assertEqual(await self.waiting_ids(), [a_map.id])

    async def test_a_failed_read_is_tried_again_at_most_three_times(self) -> None:
        a_map = await self.kept(DM)
        self.read_closely.side_effect = RuntimeError
        for _ in range(CLOSE_READ_TRIES):
            self.assertEqual(await self.sweep_one(a_map), "failed")
        self.assertEqual(await self.waiting_ids(), [])  # (left alone, counted in the log)

    async def test_a_read_with_nothing_to_keep_is_tried_again(self) -> None:
        # (a cut or empty close read: never kept as "nothing found")
        a_map = await self.kept(DM)
        self.read_closely.return_value = images.Read(
            images.MAP, "A map.", "", (images.Place("Harrowmere", "east"),), ""
        )
        self.assertEqual(await self.sweep_one(a_map), "not read")
        self.assertEqual(await self.waiting_ids(), [a_map.id])

    async def test_no_credits_or_the_days_limit_puts_it_off_with_no_try(self) -> None:
        a_map = await self.kept(DM)
        for put_off in (AIOutOfFunds("no funds"), DayLimit()):
            self.read_closely.side_effect = put_off
            for _ in range(CLOSE_READ_TRIES + 1):
                self.assertEqual(await self.sweep_one(a_map), "put off")
        self.assertEqual(await self.waiting_ids(), [a_map.id])  # (read when it can be)

    async def test_every_check_is_asked_again_and_nothing_is_claimed(self) -> None:
        a_map = await self.kept(DM)
        self.allowed = False
        self.assertEqual(await self.sweep_one(a_map), "no AI on the plan")
        self.allowed, self.consenting = True, False
        self.assertEqual(await self.sweep_one(a_map), "poster stopped")
        self.read_closely.assert_not_awaited()
        self.assertEqual(await self.waiting_ids(), [a_map.id])

    async def test_a_website_maps_note_with_no_message_restores(self) -> None:
        # (Supervisor's ruling: a map with no chat message gets a note with message 0; backups
        # must take it)
        a_map = await self.kept(DM, message_id=0)
        await self.sweep_one(a_map)
        [note] = await self.notes.notes(GUILD, self.campaign.id)  # (a new note, message 0)
        backup = await self.campaigns.export(GUILD, self.campaign.id)
        restored = await self.campaigns.import_backup(GUILD + 1, backup, DM)
        [back] = await self.notes.notes(GUILD + 1, restored.id)
        self.assertEqual(back.close, note.close)
