"""A map sent from the website (#4347, slice B; migration 0498): the queue door takes `maps-add`
from the campaign's people (not only its DMs), still takes the DM-only maps actions from DMs only,
and the answer door gives a person their own rows. Also the upload module's own checks. Invented
names. Needs Postgres."""

from __future__ import annotations

import io
import unittest
from typing import Any
from unittest import mock

from PIL import Image

from dmbot.pictures.bucket import BucketError
from dmbot.web import maps_upload, relationships
from dmbot.web.recaps import RecapError, _scoped
from tests.test_web_maps import MapsDoor

TOKEN = "0123456789abcdef0123456789abcdef"
SHA = "a" * 64


def shaped(title: str = "The Keep, floor 1") -> str:
    return f"{TOKEN} png {SHA} 12345 456 800 600 {title}"


def picture(fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(out, fmt)
    return out.getvalue()


class FakeBucket:
    def __init__(self, fail: bool = False) -> None:
        self.kept: dict[str, bytes] = {}
        self.fail = fail

    async def put(self, key: str, data: bytes) -> None:
        if self.fail:
            raise BucketError("down")
        self.kept[key] = data

    async def delete(self, key: str) -> None:
        self.kept.pop(key, None)


class TheQueueDoor(MapsDoor):
    async def test_a_player_of_the_campaign_may_queue_a_map(self) -> None:
        await self.make_bob_a_player()
        got = await self.add_action("maps-add", shaped(), who=self.player)
        self.assertEqual(got["status"], "ok", got)

    async def test_a_dm_may_queue_a_map(self) -> None:
        self.assertEqual((await self.add_action("maps-add", shaped()))["status"], "ok")

    async def test_a_member_who_is_not_one_of_the_campaigns_people_gets_nothing(self) -> None:
        self.assertIsNone(await self.add_action("maps-add", shaped(), who=self.player))

    async def test_a_person_may_queue_their_own_take_back_and_undo(self) -> None:
        await self.make_bob_a_player()
        for action, text in (("maps-takeback", self.bobs), ("maps-undo", "5")):
            got = await self.add_action(action, text, who=self.player)
            self.assertEqual(got["status"], "ok", (action, got))

    async def test_the_dm_only_actions_still_need_a_dm(self) -> None:
        await self.make_bob_a_player()
        for action, text in (
            ("maps-use", f"{self.bobs} yes"),
            ("maps-show", f"{self.bobs} shown"),
            ("maps-remove", self.bobs),
        ):
            self.assertIsNone(await self.add_action(action, text, who=self.player), action)

    async def test_other_actions_of_the_queue_still_need_a_dm_for_a_player(self) -> None:
        await self.make_bob_a_player()
        self.assertIsNone(await self.add_action("world-undo", "5", who=self.player))

    async def test_the_text_shape_is_checked(self) -> None:
        for bad in (
            "",
            "nope",
            f"{TOKEN} gif {SHA} 1 1 1 1 title",  # (not a kept kind)
            f"{TOKEN} png {SHA} 1 1 1 1 ",  # (no title)
            f"{TOKEN} png {'a' * 63} 1 1 1 1 title",  # (a short hash)
            shaped("x" * 121),  # (a title over 120)
        ):
            got = await self.add_action("maps-add", bad)
            self.assertEqual(got["status"], "bad_text", bad)

    async def test_the_longest_title_is_taken(self) -> None:
        self.assertEqual((await self.add_action("maps-add", shaped("y" * 120)))["status"], "ok")

    async def test_the_poster_reads_their_own_answer_and_not_another_persons(self) -> None:
        await self.make_bob_a_player()
        mine = await self.add_action("maps-add", shaped(), who=self.player)
        theirs = await self.add_action("maps-add", shaped())  # (Alice's)

        async def read(aid: str, who: Any) -> Any:
            async with self.web.user(who.user_id, session=who.id_hash) as conn:
                await _scoped(conn, self.g)
                cur = await conn.execute(
                    "SELECT dmbot_web_name_action_read(%s, %s, %s) AS r",
                    (self.g, self.one.id, aid),
                )
                found = await cur.fetchone()
            return found["r"] if found else None

        got = await read(mine["id"], self.player)
        self.assertEqual(got["action"], "maps-add")
        self.assertIsNone(await read(theirs["id"], self.player))


class TheUpload(MapsDoor):
    async def send(self, bucket: FakeBucket | None, *, who: Any = None, **over: Any) -> str:
        args: dict[str, Any] = {"data": picture(), "title": "Harbour", "rights": True}
        args.update(over)
        return await maps_upload.add(
            self.web, who or self.session, self.cref, bucket, args["data"], args["title"],
            rights=args["rights"],
        )  # fmt: skip

    async def test_a_picture_is_staged_and_queued_as_one_action(self) -> None:
        bucket = FakeBucket()
        answer = await self.send(bucket)
        self.assertTrue(answer)
        self.assertEqual(len(bucket.kept), 2)  # the picture and its preview
        keys = sorted(bucket.kept, key=lambda k: k.endswith("-preview.webp"))  # (the picture first)
        self.assertTrue(keys[0].startswith("p/") and keys[0].endswith(".png"))
        self.assertTrue(keys[1].endswith("-preview.webp"))
        action = await self.actions.claim_next(self.g)
        assert action is not None
        self.assertEqual(action.action, "maps-add")
        token, ext, sha, size, preview, width, height, title = action.text.split(" ", 7)
        self.assertEqual((ext, title, width, height), ("png", "Harbour", "40", "30"))
        self.assertEqual(f"p/{token}.png", keys[0])
        self.assertEqual(
            (int(size), int(preview)), (len(bucket.kept[keys[0]]), len(bucket.kept[keys[1]]))
        )
        self.assertEqual(len(sha), 64)

    async def test_no_yes_to_the_right_to_use_stores_nothing(self) -> None:
        bucket = FakeBucket()
        with self.assertRaises(RecapError) as caught:
            await self.send(bucket, rights=False)
        self.assertEqual(caught.exception.code, "needs_rights")
        self.assertEqual(bucket.kept, {})

    async def test_someone_outside_the_campaign_stores_nothing(self) -> None:
        bucket = FakeBucket()
        with self.assertRaises(RecapError) as caught:
            await self.send(bucket, who=self.player)  # (Bob: a member, not one of its people)
        self.assertEqual(caught.exception.code, "not_allowed")
        self.assertEqual(bucket.kept, {})

    async def test_a_player_of_the_campaign_may_send_one(self) -> None:
        await self.make_bob_a_player()
        bucket = FakeBucket()
        self.assertTrue(await self.send(bucket, who=self.player))

    async def test_a_file_that_is_not_a_picture_of_the_allowed_kinds_stores_nothing(self) -> None:
        for data in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"GIF89a....", b"", b"x" * 20):
            bucket = FakeBucket()
            with self.assertRaises(RecapError) as caught:
                await self.send(bucket, data=data)
            self.assertEqual(caught.exception.code, "bad_file")
            self.assertEqual(bucket.kept, {})

    async def test_a_jpeg_and_a_webp_are_taken_and_kept_with_their_own_kind(self) -> None:
        for fmt, ext in (("JPEG", "jpg"), ("WEBP", "webp")):
            bucket = FakeBucket()
            await self.send(bucket, data=picture(fmt))
            self.assertTrue(any(k.endswith(f".{ext}") for k in bucket.kept), fmt)

    async def test_a_bucket_that_fails_queues_nothing_and_leaves_nothing(self) -> None:
        bucket = FakeBucket(fail=True)
        with self.assertRaises(RecapError) as caught:
            await self.send(bucket)
        self.assertEqual(caught.exception.code, "failed")
        self.assertIsNone(await self.actions.claim_next(self.g))

    async def test_a_refused_queue_deletes_the_staged_files(self) -> None:
        bucket = FakeBucket()
        refuse = mock.AsyncMock(side_effect=RecapError("too_soon"))
        with (
            mock.patch.object(relationships, "_queue", refuse),
            self.assertRaises(RecapError),
        ):
            await self.send(bucket)
        self.assertEqual(bucket.kept, {})  # (staged, then taken away when the door said no)

    async def test_no_bucket_says_uploads_are_off(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await self.send(None)
        self.assertEqual(caught.exception.code, "uploads_off")


class TheChecks(unittest.TestCase):
    def test_the_side_cap_matches_the_doors_five_digits(self) -> None:
        self.assertEqual(maps_upload.SIDE_MAX, 99_999)

    def test_sniff_takes_png_jpeg_and_webp_by_their_bytes(self) -> None:
        self.assertTrue(maps_upload.sniff(picture("PNG")))
        self.assertTrue(maps_upload.sniff(picture("JPEG")))
        self.assertTrue(maps_upload.sniff(picture("WEBP")))

    def test_sniff_refuses_a_svg_a_gif_and_a_renamed_text(self) -> None:
        for data in (b"<svg/>", b"GIF89a", b"hello world, not a picture", b"RIFF1234WAVE"):
            self.assertFalse(maps_upload.sniff(data), data)

    def test_a_title_is_one_clean_line_of_1_to_120(self) -> None:
        self.assertEqual(maps_upload.clean_title("  The   Keep \n floor 1 "), "The Keep floor 1")
        for bad in ("", "   ", "x" * 121, "a\x00b", 5, None):
            with self.assertRaises(RecapError):
                maps_upload.clean_title(bad)


if __name__ == "__main__":
    unittest.main()
