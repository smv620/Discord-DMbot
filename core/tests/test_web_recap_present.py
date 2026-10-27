"""The recap page's "Present: ..." for each scene (#2684, #2577): the DM's read of who was in each
scene and of the characters they may pick from, and the save of one scene's list. Names only, never
a transcript word; invented names throughout."""

from __future__ import annotations

from typing import Any, cast

import httpx

from dmbot.memory.store import MemoryStore
from dmbot.recaps import beats as bt
from dmbot.web import recaps, sessions
from dmbot.web.app import create_app
from dmbot.web.payments import FakeProvider
from dmbot.web.recaps import RecapError
from tests.test_recap_web import BOB, HEADERS, NOW, Base
from tests.test_web_api import ALICE, SITE, THURSDAY, FakeDiscord, settings

AT = 100


def scene(n: int, present: tuple[str, ...] = ()) -> bt.Beat:
    return bt.Beat(n, f"Scene {n}.", ("Mira",), (n,), "story", present=present)


class Setup(Base):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.memory = MemoryStore(self.db)
        for name, player in (("Mira", 11), ("Kes", 12), ("Dorrin", 13)):
            await self.memory.add_entity(
                THURSDAY.id, self.one.id, name=name, type="player_character", source="dm",
                played_by=player, status="confirmed",
            )  # fmt: skip
        # (a character of the other campaign, and an NPC: never offered)
        await self.memory.add_entity(
            THURSDAY.id, self.two.id, name="Vex", type="player_character", source="dm",
            played_by=14, status="confirmed",
        )  # fmt: skip
        await self.memory.add_entity(
            THURSDAY.id, self.one.id, name="Old Tam", type="npc", source="dm", status="confirmed"
        )
        await self.sheet([scene(1, ("Mira", "Kes")), scene(2), scene(3, ("Dorrin",))])

    async def sheet(self, beats: list[bt.Beat]) -> None:
        await self.store.update(THURSDAY.id, self.one.id, AT, beats=bt.as_json(beats))

    async def mark(self, status: str, state: str = "ready") -> None:
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "UPDATE recap_jobs SET status = %s, state = %s WHERE guild_id = %s"
                " AND campaign_id = %s AND session_at = %s",
                (status, state, THURSDAY.id, self.one.id, AT),
            )

    async def lines(self) -> None:
        sid = f"{AT:016x}" + self.one.id[:16]
        async with self.db.guild(THURSDAY.id) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at, ended_at,"
                " line_count, speakers) VALUES (%s, %s, %s, %s, %s, 1, %s)",
                (sid, THURSDAY.id, self.one.id, AT, AT + 60, [11]),
            )
            await conn.execute(
                "INSERT INTO transcript_lines (guild_id, session_id, started_ms, user_id, heard,"
                " text) VALUES (%s, %s, 0, %s, 'raw', 'clean')",
                (THURSDAY.id, sid, 11),
            )

    async def stored(self) -> list[bt.Beat]:
        return bt.from_json((await self.state(self.one.id)).beats)

    async def bob(self) -> Any:
        token = await sessions.sign_in(self.web, cast(Any, BOB), [THURSDAY], now=NOW, days=30)
        got = await sessions.find(self.web, token, now=NOW)
        assert got is not None
        return got

    async def save(self, n: int, names: object, who: Any = None, ref: str | None = None) -> Any:
        return await recaps.set_beat_present(
            self.web, who or self.session, ref or self.ref, n, names
        )

    async def refused(self, n: int, names: object, code: str, **kw: Any) -> None:
        with self.assertRaises(RecapError) as err:
            await self.save(n, names, **kw)
        self.assertEqual(err.exception.code, code)


class Present(Setup):
    async def test_the_dm_reads_each_scene_and_the_characters_to_pick_from(self) -> None:
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertEqual(
            got["beats"],
            [
                {"n": 1, "present": ["Mira", "Kes"], "saved": False},
                {"n": 2, "present": [], "saved": False},
                {"n": 3, "present": ["Dorrin"], "saved": False},
            ],
        )
        self.assertEqual(got["characters"], ["Dorrin", "Kes", "Mira"])  # not Vex, not Old Tam

    async def test_a_recap_with_no_scenes_has_neither_key(self) -> None:
        await self.sheet([])
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertNotIn("beats", got)
        self.assertNotIn("characters", got)

    async def test_saving_keeps_the_list_on_the_scene_in_the_campaigns_order(self) -> None:
        got = await self.save(2, ["Mira", "Dorrin"])
        self.assertEqual(got, {"n": 2, "present": ["Dorrin", "Mira"], "saved": True})
        shown = (await recaps.read_recap(self.web, self.session, self.ref))["beats"]
        self.assertEqual(shown[1], {"n": 2, "present": ["Dorrin", "Mira"], "saved": True})
        self.assertEqual(shown[0]["saved"], False)  # the other scenes are untouched
        kept = await self.stored()
        self.assertEqual([b.saved for b in kept], [False, True, False])
        self.assertEqual(kept[1].present, ("Dorrin", "Mira"))
        self.assertEqual(kept[0].present, ("Mira", "Kes"))
        # (the flag survives being written out and read back, as a retry of the writer would)
        self.assertEqual(bt.from_json(bt.as_json(kept)), kept)

    async def test_an_empty_list_is_the_dms_word_too(self) -> None:
        self.assertEqual(await self.save(1, []), {"n": 1, "present": [], "saved": True})
        self.assertEqual((await self.stored())[0].present, ())
        self.assertTrue((await self.stored())[0].saved)

    async def test_a_list_can_be_changed_again(self) -> None:
        await self.save(3, ["Kes"])
        await self.save(3, ["Mira"])
        self.assertEqual((await self.stored())[2].present, ("Mira",))

    async def test_a_name_that_is_not_a_character_here_is_refused_and_nothing_is_saved(
        self,
    ) -> None:
        for names in (["Vex"], ["Old Tam"], ["mira"], ["Mira", "Mira"], [5], "Mira", None):
            with self.subTest(names=names):
                await self.refused(1, names, "bad_present")
        await self.refused(1, ["Mira"] * 13, "bad_present")
        self.assertEqual((await self.stored())[0].present, ("Mira", "Kes"))
        self.assertFalse((await self.stored())[0].saved)

    async def test_a_scene_that_is_not_there_is_stale(self) -> None:
        for n in (0, 4, -1, True, "1", None):
            with self.subTest(n=n):
                await self.refused(cast(Any, n), ["Mira"], "stale")

    async def test_a_recap_that_is_not_a_draft_ready_to_edit_is_not_allowed(self) -> None:
        await self.mark("posted")
        await self.refused(1, ["Mira"], "not_allowed")
        await self.mark("draft", "prose")
        await self.refused(1, ["Mira"], "not_allowed")

    async def test_a_player_or_another_dm_gets_nothing_and_changes_nothing(self) -> None:
        bob = await self.bob()
        with self.assertRaises(RecapError) as err:
            await recaps.read_recap(self.web, bob, self.ref)  # Bob DMs "Two", not this one
        self.assertEqual(err.exception.code, "not_found")
        await self.refused(1, ["Mira"], "not_found", who=bob)
        self.assertEqual((await self.stored())[0].present, ("Mira", "Kes"))
        # Alice is no DM of "Two": its recap is not hers to read or change either
        await self.refused(1, ["Vex"], "not_found", ref=self.other)

    async def test_the_doors_refuse_a_campaign_or_server_the_caller_is_not_a_dm_of(self) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for guild, campaign in ((THURSDAY.id, self.two.id), (999, self.one.id)):
                cur = await conn.execute(
                    "SELECT dmbot_recap_present(%s, %s, %s) AS r", (guild, campaign, AT)
                )
                self.assertIsNone((await cur.fetchone() or {})["r"])

    async def test_two_characters_with_one_name_are_listed_once_and_can_be_picked(self) -> None:
        await self.memory.add_entity(
            THURSDAY.id, self.one.id, name="Mira", type="player_character", source="dm",
            played_by=15, status="confirmed",
        )  # fmt: skip
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertEqual(got["characters"], ["Dorrin", "Kes", "Mira"])
        self.assertEqual((await self.save(1, ["Mira"]))["present"], ["Mira"])

    async def test_a_character_not_confirmed_or_without_a_player_is_not_offered(self) -> None:
        await self.memory.add_entity(
            THURSDAY.id, self.one.id, name="Pell", type="player_character", source="dm",
            played_by=16, status="proposed",
        )  # fmt: skip
        await self.memory.add_entity(
            THURSDAY.id, self.one.id, name="Quill", type="player_character", source="dm",
            status="confirmed",
        )  # fmt: skip
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertEqual(got["characters"], ["Dorrin", "Kes", "Mira"])
        await self.refused(1, ["Pell"], "bad_present")
        await self.refused(1, ["Quill"], "bad_present")

    async def test_an_odd_sheet_reads_safely_and_refuses_the_odd_scene(self) -> None:
        await self.store.update(
            THURSDAY.id, self.one.id, AT,
            beats=[7, {"what": "x", "present": "Mira", "saved": "true"}, {"what": "y"}],
        )  # fmt: skip
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertEqual(
            got["beats"],
            [{"n": 2, "present": [], "saved": False}, {"n": 3, "present": [], "saved": False}],
        )
        await self.refused(1, ["Mira"], "stale")  # position 1 is not a scene
        self.assertEqual((await self.save(2, ["Kes"]))["present"], ["Kes"])

    async def test_a_big_sheet_changes_only_the_scene_saved(self) -> None:
        await self.sheet([scene(n) for n in range(1, 121)])
        await self.save(120, ["Mira"])
        await self.refused(121, ["Mira"], "stale")
        kept = await self.stored()
        self.assertEqual(len(kept), 120)
        self.assertEqual([b.number for b in kept if b.saved], [120])
        self.assertEqual(kept[119].present, ("Mira",))

    async def test_two_saves_at_once_both_stay(self) -> None:
        import asyncio

        await asyncio.gather(self.save(1, ["Kes"]), self.save(3, ["Mira"]))
        kept = await self.stored()
        self.assertEqual((kept[0].present, kept[2].present), (("Kes",), ("Mira",)))
        self.assertEqual([b.saved for b in kept], [True, False, True])

    async def test_the_save_door_refuses_a_campaign_or_server_the_caller_is_not_a_dm_of(
        self,
    ) -> None:
        async with self.web.user(ALICE.id, session=self.session.id_hash) as conn:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(THURSDAY.id),))
            for guild, campaign in ((THURSDAY.id, self.two.id), (999, self.one.id)):
                cur = await conn.execute(
                    "SELECT dmbot_recap_set_present(%s, %s, %s, 1, '[]'::jsonb) AS r",
                    (guild, campaign, AT),
                )
                self.assertEqual((await cur.fetchone() or {})["r"], {"error": "not_found"})

    async def test_the_shared_link_never_carries_the_scenes(self) -> None:
        got = await recaps.read_recap(self.web, self.session, self.ref)
        self.assertIn("beats", got)
        listed = (await recaps.list_recaps(self.web, self.session))[0]
        self.assertNotIn("beats", listed)
        self.assertNotIn("characters", listed)

    async def test_making_it_again_clears_the_scenes_and_the_lists(self) -> None:
        await self.lines()
        await self.save(2, ["Mira"])
        await recaps.redo_recap(self.web, self.session, self.ref, replace=True)
        self.assertEqual((await self.state(self.one.id)).beats, [])
        self.assertNotIn("beats", await recaps.read_recap(self.web, self.session, self.ref))


class TheRoute(Setup):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        app = create_app(
            settings(recaps_on=True), self.web, FakeDiscord(),
            payments=FakeProvider(b"h" * 32, SITE), clock=lambda: NOW,
        )  # fmt: skip
        token = await sessions.sign_in(self.web, ALICE, [THURSDAY], now=NOW, days=30)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://web-api.internal.test",
            headers=HEADERS,
        )
        self.client.cookies.set("__Host-dmbot_session", token, domain="web-api.internal.test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await super().asyncTearDown()

    async def put(self, n: int, body: object, ref: str | None = None) -> httpx.Response:
        return await self.client.put(f"/me/recaps/{ref or self.ref}/beats/{n}/present", json=body)

    async def test_the_page_reads_and_saves_through_the_routes(self) -> None:
        got = await self.client.get(f"/me/recaps/{self.ref}")
        self.assertEqual(got.json()["characters"], ["Dorrin", "Kes", "Mira"])
        saved = await self.put(2, {"present": ["Kes"]})
        self.assertEqual(
            (saved.status_code, saved.json()), (200, {"n": 2, "present": ["Kes"], "saved": True})
        )

    async def test_each_refusal_has_its_status(self) -> None:
        cases = [
            (await self.put(1, {"present": ["Vex"]}), 422, "bad_present"),
            (await self.put(1, {"present": "Mira"}), 422, "bad_present"),
            (await self.put(1, {}), 422, "bad_present"),
            (await self.put(9, {"present": ["Mira"]}), 409, "stale"),
            (await self.put(1, {"present": ["Mira"]}, ref=self.other), 404, "not_found"),
            (await self.put(1, {"present": ["Mira"]}, ref="nonsense"), 404, "not_found"),
        ]
        for response, status, code in cases:
            with self.subTest(code=code, status=status):
                self.assertEqual((response.status_code, response.json()["error"]), (status, code))
        await self.mark("posted")
        posted = await self.put(1, {"present": ["Mira"]})
        self.assertEqual((posted.status_code, posted.json()["error"]), (409, "not_allowed"))

    async def test_signed_out_is_refused(self) -> None:
        anonymous = httpx.AsyncClient(
            transport=self.client._transport,
            base_url="https://web-api.internal.test",
            headers=HEADERS,
        )
        got = await anonymous.put(f"/me/recaps/{self.ref}/beats/1/present", json={"present": []})
        self.assertEqual(got.status_code, 401)
        await anonymous.aclose()
