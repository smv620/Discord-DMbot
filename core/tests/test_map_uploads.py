"""A map sent from the website (#4347, `maps-add`): the bot reads the staged picture, keeps it
through the one picture keeper as a map, writes one Undo and deletes the staged files. Invented
names, a generated picture and a bucket kept in memory. Needs Postgres."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image

from dmbot.maps.store import Viewer
from dmbot.memory import map_uploads
from dmbot.memory import web_actions as wa
from dmbot.pictures.bucket import BucketError
from dmbot.pictures.keeper import PictureKeeper
from tests.test_web_api import ALICE
from tests.test_web_maps import BOB, MapsDoor

TOKEN = "ab" * 16
SHA = "cd" * 32


def picture(width: int = 40, height: int = 30) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 40)).save(out, "PNG")
    return out.getvalue()


class Staging:
    """A staging bucket in memory; a missing key is the bucket's own 404."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.down = False

    async def get(self, key: str) -> bytes:
        if self.down:
            raise BucketError("ConnectionError")
        if key not in self.files:
            raise BucketError("status 404")
        return self.files[key]

    async def put(self, key: str, data: bytes) -> None:
        self.files[key] = data

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.files.pop(key, None)


class Sending(MapsDoor):
    async def make_bob_a_player(self) -> None:
        """A confirmed player character he plays: what `characters_of` (Discord's rule) counts."""
        await super().make_bob_a_player()
        await self.sql(
            "UPDATE memory_entities SET role = 'player_character', status = 'confirmed'"
            " WHERE guild_id = %s AND played_by = %s",
            self.g, BOB.id,
        )  # fmt: skip

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.staging = Staging()
        self.kept = Staging()  # (the pictures bucket)
        self.keeper = PictureKeeper(self.pictures, self.kept, 10)
        self.uploads = map_uploads.MapUploads(self.staging, self.keeper, self.maps, 10)
        self.data = picture()
        self.staging.files[f"p/{TOKEN}.png"] = self.data
        self.staging.files[f"p/{TOKEN}-preview.webp"] = b"preview"

    def action(self, user: int = ALICE.id, **text: Any) -> wa.Action:
        parts = {
            "token": TOKEN, "ext": "png", "sha": SHA, "size": len(self.data), "preview": 7,
            "width": 40, "height": 30, "title": "The Sunken Keep",
        } | text  # fmt: skip
        body = " ".join(str(parts[k]) for k in map_uploads._TEXT.groupindex)
        return wa.Action(
            id="a" * 32, campaign_id=self.one.id, entity_id="", user_id=user,
            action="maps-add", text=body,
        )  # fmt: skip

    async def send(self, user: int = ALICE.id, dms: set[int] | None = None, **text: Any) -> Any:
        return await wa.apply(
            self.memory, self.g, {ALICE.id} if dms is None else dms, self.action(user, **text),
            actions=self.actions, maps=self.maps, uploads=self.uploads,
        )  # fmt: skip


class TheApplier(Sending):
    async def test_a_dms_map_is_kept_usable_with_one_undo_and_the_staged_files_go(self) -> None:
        got = await self.send()
        self.assertEqual(got.error, "")
        self.assertTrue(got.result_id and len(got.batches) == 1)
        found = await self.maps.get(self.g, self.one.id, got.result_id, Viewer(dm=True))
        assert found is not None
        self.assertEqual(
            (found.title, found.came_from, found.audience), ("The Sunken Keep", "website", "dm")
        )
        self.assertEqual(sorted(self.staging.files), [])  # (both staged files deleted)
        self.assertEqual(
            len(self.kept.files), 2
        )  # (the picture and its preview, in the pictures bucket)
        await self.memory.undo(self.g, self.one.id, got.batches[0])
        self.assertIsNone(await self.maps.get(self.g, self.one.id, got.result_id, Viewer(dm=True)))

    async def test_a_players_map_waits_for_a_dm(self) -> None:
        await self.make_bob_a_player()
        got = await self.send(BOB.id)
        self.assertEqual(got.error, "")
        found = await self.maps.get(self.g, self.one.id, got.result_id, Viewer(dm=True))
        assert found is not None
        self.assertEqual(found.right_by, BOB.id)

    async def test_a_person_who_is_not_one_of_the_campaigns_people_is_refused(self) -> None:
        got = await self.send(BOB.id)  # (Bob plays no character in this campaign here)
        self.assertEqual(got.error, wa.NOT_ALLOWED)
        self.assertEqual(len(self.kept.files), 0)

    async def test_a_staged_file_that_is_gone_says_it_expired_and_keeps_nothing(self) -> None:
        del self.staging.files[f"p/{TOKEN}.png"]
        got = await self.send()
        self.assertEqual(got.error, map_uploads.EXPIRED)
        self.assertEqual(len(self.kept.files), 0)

    async def test_a_bucket_that_is_down_is_a_retry_not_an_expiry(self) -> None:
        self.staging.down = True
        self.assertEqual((await self.send()).error, wa.FAILED)

    async def test_a_file_that_is_not_the_queued_size_is_refused_and_cleaned_up(self) -> None:
        got = await self.send(size=len(self.data) + 1)
        self.assertEqual(got.error, map_uploads.BAD_FILE)
        self.assertEqual(sorted(self.staging.files), [])

    async def test_staged_bytes_that_are_not_a_picture_are_refused(self) -> None:
        junk = b"not a picture at all"
        self.staging.files[f"p/{TOKEN}.png"] = junk
        got = await self.send(size=len(junk))
        self.assertEqual(got.error, map_uploads.BAD_FILE)
        self.assertEqual(len(self.kept.files), 0)

    async def test_text_in_the_wrong_shape_is_refused_and_a_missing_setup_fails(self) -> None:
        bad = wa.Action(
            id="a" * 32, campaign_id=self.one.id, entity_id="", user_id=ALICE.id,
            action="maps-add", text="x",
        )  # fmt: skip
        got = await wa.apply(
            self.memory, self.g, {ALICE.id}, bad, actions=self.actions, maps=self.maps,
            uploads=self.uploads,
        )  # fmt: skip
        self.assertEqual(got.error, map_uploads.BAD_FILE)
        none = await wa.apply(
            self.memory, self.g, {ALICE.id}, self.action(), actions=self.actions, maps=self.maps,
        )  # fmt: skip
        self.assertEqual(none.error, wa.FAILED)

    async def test_the_same_map_sent_twice_is_one_map_and_the_second_says_why(self) -> None:
        first = await self.send()
        self.assertEqual(first.error, "")
        self.staging.files[f"p/{TOKEN}.png"] = self.data
        second = await self.send()
        self.assertEqual(second.error, map_uploads.MAP_REFUSED)
        self.assertTrue(second.result_other)  # (the store's own words)
        self.assertEqual(len(self.kept.files), 2)  # (nothing new was stored)

    async def test_a_player_cannot_take_back_or_undo_what_is_not_theirs(self) -> None:
        await self.make_bob_a_player()
        mine = await self.make_map(BOB.id, "Bob's second", dm=False)
        theirs = await wa.apply(
            self.memory, self.g, {ALICE.id},
            wa.Action(
                id="b" * 32, campaign_id=self.one.id, entity_id="", user_id=BOB.id,
                action="maps-takeback", text=self.dm_map,
            ),
            actions=self.actions, maps=self.maps,
        )  # fmt: skip
        self.assertEqual(theirs.error, wa.NOT_ALLOWED)  # (the DM's map is not Bob's to take back)
        undo = await wa.apply(
            self.memory, self.g, {ALICE.id},
            wa.Action(
                id="c" * 32, campaign_id=self.one.id, entity_id="", user_id=BOB.id,
                action="maps-undo", text="1",
            ),
            actions=self.actions, maps=self.maps,
        )  # fmt: skip
        self.assertEqual(undo.error, wa.GONE)  # (no done action of Bob's made that change)
        own = await wa.apply(
            self.memory, self.g, {ALICE.id},
            wa.Action(
                id="d" * 32, campaign_id=self.one.id, entity_id="", user_id=BOB.id,
                action="maps-takeback", text=mine,
            ),
            actions=self.actions, maps=self.maps,
        )  # fmt: skip
        self.assertEqual(own.error, "")
