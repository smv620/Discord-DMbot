"""Kept pictures on the website (#3274): the open link and the campaign's gallery list.

The link must answer a public picture with a short-lived signed address, a private one only to a
signed-in person of the campaign's server, a removed one with a few plain words, and say nothing
about the poster, the description or the campaign. The list shows any member the campaign's own
pictures, newest first, with the filters, and the usage line to the game masters only."""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Any
from unittest.mock import patch

import httpx
from psycopg.types.json import Jsonb

from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.web import pictures, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, NOW, Base
from tests.test_web_api import ALICE, API, THURSDAY, FakeDiscord, settings

START = 1_700_000_000
MB = 1024 * 1024


def code() -> str:
    return secrets.token_urlsafe(32)


def sign(key: str) -> str:
    return f"https://bucket.example/{key}?signed"


class Kept(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.cref = f"{THURSDAY.id}.{self.one.id}"
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.session_with_lines(START)
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE transcript_sessions SET speaker_names = %s WHERE id = %s",
                (Jsonb({str(ALICE.id): "Mira", str(BOB.id): "Cerric"}), self.sid),
            )

    async def session_with_lines(self, at: int) -> str:
        """A session with a line, so the transcripts page numbers it (an empty one has none)."""
        sid = await self.transcripts.open_session(THURSDAY.id, self.one.id, at)
        await self.transcripts.add_lines(
            THURSDAY.id, sid, [Line(at * 1000 + 1000, 1, "we cross the river", "we cross")]
        )
        await self.transcripts.end_session(THURSDAY.id, sid, at + 3600)
        return sid

    async def keep(
        self,
        campaign: str | None = None,
        *,
        poster: int = 1001,
        description: str = "a map",
        public: bool = True,
        permanent: bool = False,
        at: int = START + 10,
        sid: str | None = None,
        gone: bool = False,
        for_room: bool = False,
        size: int = 2 * MB,
    ) -> str:
        link = code()
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO campaign_pictures (guild_id, campaign_id, poster_id, session_id,"
                " description, code, public, permanent, consent_version, right_granted_at, sha256,"
                " object_key, preview_key, size_bytes, preview_bytes, made_at, deleted_at,"
                " removed_for_room) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,7,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    THURSDAY.id,
                    campaign or self.one.id,
                    poster,
                    sid if sid is not None else self.sid,
                    description,
                    link,
                    public,
                    permanent and not gone,
                    at,
                    secrets.token_hex(32),
                    f"{THURSDAY.id}/k/{link[:8]}.png",
                    f"{THURSDAY.id}/k/{link[:8]}.webp",
                    size,
                    size // 10,
                    at,
                    at + 1 if gone else None,
                    for_room,
                ),
            )
        return link

    async def signed_in(self, user: Any) -> Any:
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        found = await sessions.find(self.web, token, now=NOW)
        assert found is not None
        return found

    async def gallery(self, who: Any | None = None, **filters: Any) -> dict[str, Any]:
        return await pictures.read_pictures(
            self.web,
            who or self.session,
            self.cref,
            storage_mb=500,
            api_url="https://api.example",
            **filters,
        )


class TheOpenLink(Kept):
    async def test_a_public_picture_redirects_to_a_signed_address_with_no_sign_in(self) -> None:
        link = await self.keep()
        got = await pictures.resolve_link(self.web, link, None, sign=sign)
        assert got is not None
        self.assertEqual(got.kind, "redirect")
        self.assertTrue(got.url.startswith(f"https://bucket.example/{THURSDAY.id}/k/"))
        self.assertTrue(got.url.endswith(".png?signed"))

    async def test_the_preview_address_is_the_previews(self) -> None:
        link = await self.keep()
        got = await pictures.resolve_link(self.web, link, None, sign=sign, preview=True)
        assert got is not None
        self.assertIn(".webp", got.url)

    async def test_a_private_picture_is_for_a_signed_in_person_of_the_server_only(self) -> None:
        link = await self.keep(public=False)
        nobody = await pictures.resolve_link(self.web, link, None, sign=sign)
        assert nobody is not None
        self.assertEqual((nobody.kind, nobody.words), ("private", pictures.PRIVATE_WORDS))
        member = await pictures.resolve_link(self.web, link, self.session, sign=sign)
        assert member is not None
        self.assertEqual(member.kind, "redirect")
        token = await sessions.sign_in(self.web, ALICE, [], now=NOW, days=30)  # in no server
        outsider = await sessions.find(self.web, token, now=NOW)
        assert outsider is not None
        refused = await pictures.resolve_link(self.web, link, outsider, sign=sign)
        assert refused is not None
        self.assertEqual(refused.kind, "private")

    async def test_a_removed_picture_says_so_and_why_and_gives_no_address(self) -> None:
        link = await self.keep(gone=True)
        room = await self.keep(gone=True, for_room=True)
        a = await pictures.resolve_link(self.web, link, None, sign=sign)
        b = await pictures.resolve_link(self.web, room, None, sign=sign)
        assert a is not None and b is not None
        self.assertEqual((a.kind, a.words, a.url), ("removed", pictures.REMOVED_WORDS, ""))
        self.assertEqual(b.words, pictures.REMOVED_FOR_ROOM_WORDS)

    async def test_a_code_that_names_nothing_is_not_found(self) -> None:
        self.assertIsNone(await pictures.resolve_link(self.web, code(), None, sign=sign))
        self.assertIsNone(await pictures.resolve_link(self.web, "short", None, sign=sign))
        self.assertIsNone(await pictures.resolve_link(self.web, "x" * 50 + "!", None, sign=sign))

    async def test_without_a_bucket_key_the_link_says_it_is_not_open_yet(self) -> None:
        link = await self.keep()
        got = await pictures.resolve_link(self.web, link, None, sign=None)
        assert got is not None
        self.assertEqual((got.kind, got.words), ("not_open", pictures.NOT_OPEN_WORDS))

    async def test_the_door_tells_nothing_about_the_table(self) -> None:
        link = await self.keep(description="THE HIDDEN KING IS THE FERRYMAN")
        async with self.web.anonymous() as conn:
            cur = await conn.execute("SELECT dmbot_picture_by_code(%s) AS r", (link,))
            row = await cur.fetchone()
        assert row is not None
        text = repr(row["r"])
        for word in ("FERRYMAN", "Mira", self.one.id, "poster", "description"):
            self.assertNotIn(word, text)

    async def test_the_website_role_cannot_read_a_row_even_with_the_setting_set_itself(
        self,
    ) -> None:
        link = await self.keep()
        async with self.web.anonymous() as conn:
            await conn.execute("SELECT set_config('dmbot.picture_link_code', %s, true)", (link,))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT count(*) AS n FROM campaign_pictures")

    async def test_the_door_leaves_the_setting_empty_afterwards(self) -> None:
        link = await self.keep()
        async with self.web.anonymous() as conn:
            await conn.execute("SELECT dmbot_picture_by_code(%s)", (link,))
            cur = await conn.execute("SELECT dmbot_picture_link_code() AS c")
            row = await cur.fetchone()
            assert row is not None
            self.assertIn(row["c"], (None, ""))

    async def test_the_door_never_lists_and_the_table_is_closed_to_the_website_role(self) -> None:
        await self.keep()
        async with self.web.anonymous() as conn:
            cur = await conn.execute("SELECT dmbot_picture_by_code('') AS r")
            row = await cur.fetchone()
            assert row is not None
            self.assertIsNone(row["r"])
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM campaign_pictures")


class TheSignedAddress(Kept):
    def test_it_names_the_object_for_a_few_minutes_and_is_the_same_every_time(self) -> None:
        now = dt.datetime(2026, 11, 2, 12, 0, 0, tzinfo=dt.UTC)
        args: dict[str, Any] = {
            "account": "acct",
            "bucket": "pics",
            "key": "1/2/abc.png",
            "access_key": "AKEXAMPLE",
            "secret_key": "not-a-real-secret",
            "now": now,
        }
        first = pictures.presign_get(**args)
        self.assertEqual(first, pictures.presign_get(**args))
        self.assertTrue(first.startswith("https://acct.r2.cloudflarestorage.com/pics/1/2/abc.png?"))
        self.assertIn("X-Amz-Expires=300", first)
        self.assertIn("X-Amz-Date=20261102T120000Z", first)
        self.assertRegex(first, r"X-Amz-Signature=[0-9a-f]{64}$")
        other = pictures.presign_get(**{**args, "key": "1/2/other.png"})
        self.assertNotEqual(first.rsplit("=", 1)[1], other.rsplit("=", 1)[1])
        self.assertNotIn("not-a-real-secret", first)


class TheGallery(Kept):
    async def test_any_member_sees_the_campaigns_pictures_newest_first_with_names(self) -> None:
        old = await self.keep(description="old map", at=START + 10)
        new = await self.keep(description="new map", at=START + 20, poster=2002)
        got = await self.gallery()
        self.assertEqual([p["code"] for p in got["pictures"]], [new, old])
        first = got["pictures"][0]
        self.assertEqual(
            (first["poster"], first["sessionNumber"], first["isPublic"], first["mine"]),
            ("Cerric", 1, True, False),
        )
        self.assertEqual(first["previewUrl"], f"https://api.example/pictures/{new}/preview")
        self.assertTrue(got["pictures"][1]["mine"])  # Alice posted the old one
        self.assertEqual((got["sessions"], got["posters"]), ([1], ["Cerric", "Mira"]))

    async def test_a_player_sees_them_without_the_usage_and_a_dm_sees_it(self) -> None:
        await self.keep(size=10 * MB)
        await self.keep(size=20 * MB, permanent=True)
        dm = await self.gallery()
        self.assertTrue(dm["isDm"])
        self.assertEqual(
            dm["usage"], {"usedMb": 33, "limitMb": 500, "permanentMb": 22, "permanentLimitMb": 400}
        )
        player = await self.gallery(await self.signed_in(BOB))  # Bob only plays in "One"
        self.assertFalse(player["isDm"])
        self.assertIsNone(player["usage"])
        self.assertEqual(len(player["pictures"]), 2)

    async def test_private_pictures_are_listed_to_members_and_marked(self) -> None:
        await self.keep(public=False)
        got = await self.gallery(await self.signed_in(BOB))
        self.assertEqual([p["isPublic"] for p in got["pictures"]], [False])

    async def test_only_this_campaigns_unremoved_pictures_are_listed_and_no_key_leaves(
        self,
    ) -> None:
        mine = await self.keep()
        await self.keep(self.two.id, description="OTHER CAMPAIGN")
        await self.keep(gone=True, description="REMOVED ONE")
        got = await self.gallery()
        self.assertEqual([p["code"] for p in got["pictures"]], [mine])
        text = repr(got)
        for word in ("OTHER CAMPAIGN", "REMOVED ONE", ".png", ".webp", "object_key", "sha256"):
            self.assertNotIn(word, text)

    async def test_the_filters_narrow_by_session_poster_and_words(self) -> None:
        other = await self.session_with_lines(START + 5000)
        a = await self.keep(description="A map of the ford", poster=1001)
        b = await self.keep(description="Cerric's oath scroll", poster=2002, at=START + 20)
        c = await self.keep(description="A storm", sid=other, at=START + 30)
        self.assertEqual(
            [p["code"] for p in (await self.gallery(session_number=2))["pictures"]], [c]
        )
        self.assertEqual(
            [p["code"] for p in (await self.gallery(poster="Cerric"))["pictures"]], [b]
        )
        self.assertEqual([p["code"] for p in (await self.gallery(q="MAP"))["pictures"]], [a])
        self.assertEqual((await self.gallery(session_number=9))["pictures"], [])
        self.assertEqual((await self.gallery(session_number="x"))["pictures"], [])

    async def test_a_percent_or_underscore_in_the_search_is_just_that_character(self) -> None:
        await self.keep(description="plain words")
        hundred = await self.keep(description="100% sure")
        self.assertEqual([p["code"] for p in (await self.gallery(q="%"))["pictures"]], [hundred])
        self.assertEqual((await self.gallery(q="_"))["pictures"], [])

    async def test_it_pages_with_before_and_says_when_more_remain(self) -> None:
        made = [await self.keep(at=START + 100 + i) for i in range(pictures.PAGE + 3)]
        first = await self.gallery()
        self.assertEqual(len(first["pictures"]), pictures.PAGE)
        self.assertTrue(first["more"])
        self.assertEqual(first["pictures"][0]["code"], made[-1])
        rest = await self.gallery(before=first["pictures"][-1]["at"])
        self.assertEqual(len(rest["pictures"]), 3)
        self.assertFalse(rest["more"])

    async def test_pictures_posted_in_the_same_second_are_never_skipped_by_paging(self) -> None:
        made = [await self.keep(at=START + 500) for _ in range(pictures.PAGE + 5)]
        first = await self.gallery()
        self.assertEqual(len(first["pictures"]), pictures.PAGE)
        rest = await self.gallery(before=first["pictures"][-1]["cursor"])
        seen = [p["code"] for p in first["pictures"] + rest["pictures"]]
        self.assertEqual(sorted(seen), sorted(made))
        self.assertFalse(rest["more"])

    async def test_a_cursor_that_is_not_one_starts_from_the_top(self) -> None:
        self.assertEqual(pictures._cursor("99999999999999999999"), (0, 0))
        self.assertEqual(pictures._cursor("1:99999999999999999999"), (0, 0))
        self.assertEqual(pictures._cursor("nonsense"), (0, 0))
        self.assertEqual(pictures._cursor(None), (0, 0))
        self.assertEqual(pictures._cursor("17"), (17, 0))
        self.assertEqual(pictures._cursor("17:4"), (17, 4))

    async def test_another_servers_campaign_is_refused_by_the_door_itself(self) -> None:
        async with self.web.user(ALICE.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_pictures(%s, %s, 0, '', '', 0, 0, 24) AS a,"
                " dmbot_web_pictures(%s, %s, 0, '', '', 0, 0, 24) AS b",
                (999, self.one.id, THURSDAY.id, "0" * 32),
            )
            row = await cur.fetchone()
            assert row is not None
            self.assertEqual((row["a"], row["b"]), (None, None))

    async def test_a_stranger_and_an_unknown_campaign_are_refused(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await pictures.read_pictures(
                self.web, self.session, f"999.{self.one.id}", storage_mb=500, api_url="x"
            )
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(RecapError) as unknown:
            await pictures.read_pictures(
                self.web, self.session, f"{THURSDAY.id}.{'0' * 32}", storage_mb=500, api_url="x"
            )
        self.assertEqual(unknown.exception.code, "not_allowed")

    async def test_a_name_the_session_never_kept_reads_someone(self) -> None:
        await self.keep(poster=7777)
        self.assertEqual((await self.gallery())["pictures"][0]["poster"], "Someone")


async def _no_sleep(_seconds: float) -> None:
    return None


class TheActions(Kept):
    """A picture's Make private/public, Permanent and Delete are queued for the bot, which applies
    them; the website's door checks only who may ask and reads the bot's answer back."""

    async def queued(self, link: str | None = None) -> list[dict[str, Any]]:
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute(
                "SELECT a.action, a.user_id, a.state, p.code FROM picture_actions a"
                " JOIN campaign_pictures p ON p.id = a.picture_id ORDER BY a.seq"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def answer(self, state: str, note: str = "") -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE picture_actions SET state = %s, note = %s WHERE state = 'waiting'",
                (state, note),
            )

    async def act(self, who: Any, link: str, action: str, **kw: Any) -> dict[str, Any]:
        return await pictures.act_on_picture(
            self.web, who, self.cref, link, action, sleep=_no_sleep, wait=0, **kw
        )

    async def test_the_poster_queues_make_private_and_delete_and_a_dm_can_too(self) -> None:
        mine = await self.keep(poster=ALICE.id)  # Alice posted it and is the DM
        bob = await self.signed_in(BOB)
        theirs = await self.keep(poster=BOB.id)
        self.assertEqual((await self.act(bob, theirs, "private"))["state"], "waiting")
        self.assertEqual((await self.act(bob, theirs, "delete"))["state"], "waiting")
        self.assertEqual((await self.act(self.session, theirs, "public"))["state"], "waiting")
        self.assertEqual((await self.act(self.session, mine, "delete"))["state"], "waiting")
        got = await self.queued()
        self.assertEqual([a["action"] for a in got], ["private", "delete", "public", "delete"])
        self.assertEqual([a["user_id"] for a in got], [BOB.id, BOB.id, ALICE.id, ALICE.id])

    async def test_a_player_cannot_touch_another_players_picture_or_set_permanent(self) -> None:
        bob = await self.signed_in(BOB)  # a player in this campaign
        alices = await self.keep(poster=ALICE.id)
        for action in ("private", "delete", "permanent", "unpermanent"):
            with self.assertRaises(pictures.PictureRefused) as caught:
                await self.act(bob, alices, action)
            self.assertEqual(caught.exception.code, "not_allowed")
        own = await self.keep(poster=BOB.id)
        with self.assertRaises(pictures.PictureRefused) as caught:
            await self.act(bob, own, "permanent")  # the poster, but not a DM
        self.assertEqual(caught.exception.code, "not_allowed")
        self.assertEqual(await self.queued(), [])

    async def test_a_dm_sets_and_clears_permanent(self) -> None:
        link = await self.keep(poster=BOB.id)
        await self.act(self.session, link, "permanent")
        await self.act(self.session, link, "unpermanent")
        self.assertEqual([a["action"] for a in await self.queued()], ["permanent", "unpermanent"])

    async def test_a_removed_unknown_or_other_campaigns_picture_is_not_found(self) -> None:
        gone = await self.keep(gone=True)
        other = await self.keep(self.two.id)
        for link in (gone, other, code()):
            with self.assertRaises(pictures.PictureRefused) as caught:
                await self.act(self.session, link, "delete")
            self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(pictures.PictureRefused):
            await self.act(self.session, "short", "delete")
        with self.assertRaises(pictures.PictureRefused) as bad:
            await self.act(self.session, await self.keep(), "explode")
        self.assertEqual(bad.exception.code, "bad_action")

    async def test_it_reports_the_bots_answer_and_its_refusal_words(self) -> None:
        link = await self.keep()

        async def bot_answers(_s: float) -> None:
            await self.answer("done")

        done = await pictures.act_on_picture(
            self.web, self.session, self.cref, link, "private", sleep=bot_answers, wait=5
        )
        self.assertEqual(done["state"], "done")

        async def bot_refuses(_s: float) -> None:
            await self.answer(
                "refused", "Permanent pictures are full (400 MB). Unmark one to make room."
            )

        with self.assertRaises(pictures.PictureRefused) as caught:
            await pictures.act_on_picture(
                self.web, self.session, self.cref, link, "permanent", sleep=bot_refuses, wait=5
            )
        self.assertEqual(caught.exception.code, "permanent-full")
        self.assertIn("Permanent pictures are full (400 MB)", caught.exception.words)

    async def test_one_person_cannot_fill_the_queue_and_the_campaign_has_a_cap_too(self) -> None:
        link = await self.keep()
        for _ in range(5):
            await self.act(self.session, link, "private")
        with self.assertRaises(pictures.PictureRefused) as caught:
            await self.act(self.session, link, "private")  # a sixth of one person's
        self.assertEqual(caught.exception.code, "too_soon")
        bob = await self.signed_in(BOB)
        own = await self.keep(poster=BOB.id)
        await self.act(bob, own, "private")  # someone else still gets in
        async with self.db.guild(THURSDAY.id) as conn:  # fill the campaign to its 20
            for n in range(14):
                await conn.execute(
                    "INSERT INTO picture_actions (guild_id, campaign_id, id, user_id, picture_id,"
                    " action, created_at) VALUES (%s, %s, %s, 77, 1, 'private', %s)",
                    (THURSDAY.id, self.one.id, f"{n:032x}", NOW),
                )
        with self.assertRaises(pictures.PictureRefused) as full:
            await self.act(bob, own, "private")
        self.assertEqual(full.exception.code, "too_soon")
        with self.assertRaises(RecapError):
            await pictures.act_on_picture(
                self.web, self.session, f"999.{self.one.id}", link, "private", sleep=_no_sleep
            )

    async def test_only_the_person_who_asked_can_read_the_answer_by_sql(self) -> None:
        link = await self.keep()
        await self.act(self.session, link, "private")
        async with self.db.guild(THURSDAY.id) as conn:
            cur = await conn.execute("SELECT id FROM picture_actions")
            row = await cur.fetchone()
            assert row is not None
            aid = row["id"]
        bob = await self.signed_in(BOB)
        for who, expect in ((self.session, True), (bob, False)):
            async with self.web.user(who.user_id, session=who.id_hash) as conn:
                await conn.execute(
                    "SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),)
                )
                cur = await conn.execute(
                    "SELECT dmbot_web_picture_action(%s, %s, %s) AS r",
                    (THURSDAY.id, self.one.id, aid),
                )
                got = await cur.fetchone()
                assert got is not None
                self.assertEqual(got["r"] is not None, expect)

    async def test_the_website_role_cannot_read_the_queue_table(self) -> None:
        async with self.web.user(ALICE.id, session="x") as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            with self.assertRaises(Exception):  # noqa: B017 (permission denied, whichever error)
                await conn.execute("SELECT * FROM picture_actions")


class TheActionRoute(Kept):
    """POST /me/campaigns/{ref}/pictures/{code}: the statuses the page relies on."""

    async def client(self, user: Any, *, header: bool = True) -> httpx.AsyncClient:
        app = create_app(
            settings(), self.web, FakeDiscord(), payments=FakeProvider(b"h" * 32, "https://x"),
            clock=lambda: NOW,
        )  # fmt: skip
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=API,
            headers={"X-DMbot-Request": "1"} if header else {},
        )  # fmt: skip
        token = await sessions.sign_in(self.web, user, [THURSDAY], now=NOW, days=30)
        client.cookies.set("__Host-dmbot_session", token, domain="api.dmbot.example")
        return client

    async def post(self, client: httpx.AsyncClient, link: str, body: Any) -> httpx.Response:
        return await client.post(f"/me/campaigns/{self.cref}/pictures/{link}", json=body)

    async def test_a_queued_change_answers_202_waiting_when_the_bot_has_not_answered(self) -> None:
        link = await self.keep()
        async with await self.client(ALICE) as client:
            with patch.object(pictures, "WAIT_SECONDS", 0.0):
                got = await self.post(client, link, {"action": "private"})
        self.assertEqual((got.status_code, got.json()["state"]), (202, "waiting"))

    async def test_a_refusal_a_stranger_and_a_bad_request_get_their_own_status(self) -> None:
        link = await self.keep(poster=BOB.id)
        async with await self.client(BOB) as client:
            denied = await self.post(client, link, {"action": "permanent"})  # a player
            self.assertEqual((denied.status_code, denied.json()["detail"]), (403, "not_allowed"))
            missing = await self.post(client, code(), {"action": "delete"})
            self.assertEqual(missing.status_code, 404)
            bad = await self.post(client, link, {"action": "explode"})
            self.assertEqual(bad.status_code, 400)
            nothing = await client.post(
                f"/me/campaigns/{self.cref}/pictures/{link}", content=b"not json"
            )
            self.assertEqual(nothing.status_code, 400)
        async with await self.client(ALICE) as client:
            for _ in range(5):
                with patch.object(pictures, "WAIT_SECONDS", 0.0):
                    await self.post(client, link, {"action": "private"})
            slow = await self.post(client, link, {"action": "private"})
            self.assertEqual(slow.status_code, 429)

    async def test_the_bots_refusal_comes_back_as_409_with_its_words(self) -> None:
        link = await self.keep()
        words = "Permanent pictures are full (400 MB). Unmark one to make room."

        async def bot_refuses(_s: float) -> None:
            async with self.db.guild(THURSDAY.id) as conn:
                await conn.execute(
                    "UPDATE picture_actions SET state = 'refused', note = %s"
                    " WHERE state = 'waiting'",
                    (words,),
                )

        async with await self.client(ALICE) as client:
            with patch("dmbot.web.pictures.asyncio.sleep", bot_refuses):
                got = await self.post(client, link, {"action": "permanent"})
        self.assertEqual(got.status_code, 409)
        self.assertEqual((got.json()["detail"], got.json()["note"]), ("permanent-full", words))

    async def test_a_post_without_the_request_header_is_refused(self) -> None:
        link = await self.keep()
        async with await self.client(ALICE, header=False) as client:
            got = await self.post(client, link, {"action": "private"})
        self.assertEqual(got.status_code, 403)
        self.assertEqual(await TheActions.queued(self), [])  # type: ignore[arg-type]

    async def test_a_signed_out_post_is_401(self) -> None:
        link = await self.keep()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(
                    settings(),
                    self.web,
                    FakeDiscord(),
                    payments=FakeProvider(b"h" * 32, "https://x"),
                    clock=lambda: NOW,
                )
            ),
            base_url=API,
            headers={"X-DMbot-Request": "1"},
        ) as client:
            got = await self.post(client, link, {"action": "private"})
        self.assertEqual(got.status_code, 401)
