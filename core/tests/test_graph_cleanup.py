"""The graph clean-up's door, count and scene sweep (#3740), on Postgres: the DM gets the isolated
groups, anyone else nothing; the Discord count agrees with the page; and an isolated entry keeps
its scene until it is linked. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.scenes import ENTITY, Scene, Scenes
from dmbot.transcript.models import Line, line_ref
from dmbot.transcript.store import TranscriptStore
from dmbot.web import graph_cleanup
from dmbot.web.recaps import RecapError
from tests.test_web_all_names import MEMBER
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import BOB_USER, TheRoute, Web


class Isolated(Web):
    """The fixture plus a pair linked only to each other (no tests of its own, so the classes
    below do not run each other's)."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (the fixture links Mira to Rook, a player character; the drunk and the sailors are
        # linked only to each other)
        self.drunk = await self.add("Old Fenn", "character", role="npc")
        self.sailors = await self.add("The Salt Crew", "faction")
        await self.link("knows", self.drunk, self.sailors, "drunk-sailors")


class TheCleanup(Isolated):
    async def test_the_dm_gets_the_isolated_group_and_the_count_agrees(self) -> None:
        page = await graph_cleanup.read_cleanup(self.web, self.session, self.cref)
        ids = [sorted(e["id"] for e in g["entries"]) for g in page["groups"]]
        self.assertIn(sorted([self.drunk, self.sailors]), ids)
        self.assertNotIn(self.mira, {i for g in ids for i in g})  # (linked to Rook)
        self.assertEqual(page["total"], await self.memory.isolated_count(self.g, self.one.id))

    async def test_anyone_but_the_dm_gets_nothing(self) -> None:
        with self.assertRaises(RecapError) as caught:
            await graph_cleanup.read_cleanup(self.web, self.player, self.cref)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_another_campaign_s_dm_or_another_server_gets_nothing(self) -> None:
        # Bob DMs "Two" in this server: reading "One" he gets nothing
        as_bob = await self.signed(BOB_USER, THURSDAY)
        elsewhere = await self.signed(
            ALICE, MEMBER.__class__(id=999, name="Elsewhere", manage=True)
        )
        for who in (as_bob, elsewhere):
            with self.assertRaises(RecapError) as caught:
                await graph_cleanup.read_cleanup(self.web, who, self.cref)
            self.assertEqual(caught.exception.code, "not_found")

    async def test_a_campaign_with_no_player_character_has_nothing_isolated(self) -> None:
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        await self.memory.add_entity(
            self.g, other.id, type="character", name="Lone Figure", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        self.assertEqual(await self.memory.isolated_count(self.g, other.id), 0)
        page = await graph_cleanup.read_cleanup(self.web, self.session, f"{self.g}.{other.id}")
        self.assertEqual((page["groups"], page["total"]), ([], 0))

    async def test_a_guess_connection_ties_nobody_and_a_waiting_one_does(self) -> None:
        async def listed() -> set[str]:
            page = await graph_cleanup.read_cleanup(self.web, self.session, self.cref)
            assert await self.memory.isolated_count(self.g, self.one.id) == page["total"]
            return {e["id"] for g in page["groups"] for e in g["entries"]}

        # (what someone believes, held as a secret waiting connection, is no connection, #2412)
        await self.memory.add_relation(
            self.g, self.one.id, self.rook, "knows", self.drunk, source="extractor",
            confidence=0.5, status="proposed", secret=True,
        )  # fmt: skip
        self.assertIn(self.drunk, await listed())
        await self.memory.add_relation(
            self.g, self.one.id, self.rook, "knows", self.sailors, source="scan",
            confidence=0.8, status="proposed",
        )  # fmt: skip
        self.assertFalse({self.drunk, self.sailors} & await listed())

    async def test_linking_one_to_the_party_takes_the_group_off(self) -> None:
        await self.link("knows", self.rook, self.drunk, "rook-drunk")
        page = await graph_cleanup.read_cleanup(self.web, self.session, self.cref)
        listed = {e["id"] for g in page["groups"] for e in g["entries"]}
        self.assertFalse({self.drunk, self.sailors} & listed)

    async def test_an_isolated_entry_keeps_its_scene_until_it_is_linked(self) -> None:
        scenes = Scenes(self.memory)
        await scenes.keep(
            self.g, self.one.id,
            [Scene(ENTITY, self.drunk, "He sat with the sailors all night.", 3),
             Scene(ENTITY, self.mira, "She met Rook at the gate.", 2)],
            opted_in=set(), prompt_version="p1", model="m1",
        )  # fmt: skip
        await scenes.sweep(self.g, self.one.id)
        page = await graph_cleanup.read_cleanup(self.web, self.session, self.cref)
        drunk = next(e for g in page["groups"] for e in g["entries"] if e["id"] == self.drunk)
        self.assertEqual(drunk["scene"], "He sat with the sailors all night.")
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT subject_id FROM memory_scenes WHERE campaign_id = %s", (self.one.id,)
            )
            kept = {str(r["subject_id"]) for r in await cur.fetchall()}
        self.assertEqual(kept, {self.drunk})  # (Mira is linked and named: hers went)
        await self.link("knows", self.rook, self.drunk, "rook-drunk")
        await scenes.sweep(self.g, self.one.id)
        async with self.db.guild(self.g) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_scenes WHERE campaign_id = %s", (self.one.id,)
            )
            self.assertEqual((await cur.fetchone() or {})["n"], 0)


class WhereItWasSaid(Isolated):
    """ "Where was it said?" for a kept isolated entry (#3767 item 1), and the page's word for a
    campaign with no player character (item 2)."""

    async def said(self) -> None:
        tx = TranscriptStore(self.db)
        sid = await tx.open_session(self.g, self.one.id, 1000)
        lines = [
            Line(61_000, 777, "The road was long.", "The road was long."),
            Line(65_000, ALICE.id, "Old Fenn bought the round.", "Old Fenn bought the round."),
            Line(70_000, 778, "It was cold there.", "It was cold there."),
        ]
        await tx.add_lines(self.g, sid, lines)
        await self.memory.add_mention(
            self.g, self.one.id, self.drunk, line_ref=line_ref(sid, 65_000, ALICE.id),
            span=(0, 8), confidence=1.0, method="exact", source="scan", session_started_at=1000,
        )  # fmt: skip
        async with self.db.guild(self.g) as conn:
            for user in (ALICE.id, 777):  # (778 never agreed)
                await conn.execute(
                    "INSERT INTO consent (guild_id, user_id, granted_at) VALUES (%s, %s, 0)"
                    " ON CONFLICT DO NOTHING",
                    (self.g, user),
                )

    async def test_a_kept_isolated_entry_has_its_places_and_a_player_gets_nothing(self) -> None:
        await self.said()
        got = await graph_cleanup.read_places(self.web, self.session, self.cref, self.drunk)
        (place,) = got["places"]
        texts = [line.get("text") for line in place["lines"]]
        self.assertIn("Old Fenn bought the round.", texts)
        self.assertNotIn("It was cold there.", texts)  # (778 never agreed)
        self.assertIn({"removed": True}, place["lines"])
        road = next(x for x in place["lines"] if x.get("text") == "The road was long.")
        self.assertEqual(road["speaker"], "Someone")  # (777 agrees; no kept speaker name)
        self.assertEqual(got["morePlaces"], 0)
        for who, entity in ((self.player, self.drunk), (self.session, self.keep)):  # (a place)
            with self.assertRaises(RecapError) as caught:
                await graph_cleanup.read_places(self.web, who, self.cref, entity)
            self.assertEqual(caught.exception.code, "not_found")

    async def test_a_secret_other_name_does_not_hide_it_from_the_dm(self) -> None:
        await self.said()
        await self.alias(self.drunk, "The Pale Hand", secret=True)
        got = await graph_cleanup.read_places(self.web, self.session, self.cref, self.drunk)
        self.assertEqual(len(got["places"]), 1)

    async def refused_places(self, who: Any, ref: str, entity: str) -> None:
        with self.assertRaises(RecapError) as caught:
            await graph_cleanup.read_places(self.web, who, ref, entity)
        self.assertEqual(caught.exception.code, "not_found")

    async def test_another_campaign_s_dm_its_ids_and_gone_entries_get_nothing(self) -> None:
        await self.said()
        as_bob = await self.signed(BOB_USER, THURSDAY)  # (Bob DMs "Two" in this server)
        await self.refused_places(as_bob, self.cref, self.drunk)
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        stranger = await self.memory.add_entity(
            self.g, other.id, type="character", name="Stranger", source=DM_SOURCE,
            status="confirmed",
        )  # fmt: skip
        await self.refused_places(self.session, self.cref, stranger.value.id)  # (through One's ref)
        await self.memory.merge(
            self.g, self.one.id, self.mira, self.drunk, source="dm", dm_said_same=True
        )
        await self.refused_places(self.session, self.cref, self.drunk)  # (merged away)
        await self.memory.set_entity_status(
            self.g, self.one.id, self.sailors, "rejected", source="dm"
        )
        await self.refused_places(self.session, self.cref, self.sailors)  # (removed)

    async def test_the_page_says_whether_a_player_character_is_set(self) -> None:
        page = await graph_cleanup.read_cleanup(self.web, self.session, self.cref)
        self.assertFalse(page["noPlayerCharacter"])
        other = await self.campaigns.create(self.g, "Other", ALICE.id)
        empty = await graph_cleanup.read_cleanup(self.web, self.session, f"{self.g}.{other.id}")
        self.assertTrue(empty["noPlayerCharacter"])


class TheCleanupRoute(TheRoute):
    async def test_the_dm_gets_the_page_and_a_player_a_404(self) -> None:
        drunk = await self.add("Old Fenn", "character", role="npc")
        async with await self.client(self.session) as client:
            got = await client.get(f"/me/campaigns/{self.cref}/web/cleanup")
            self.assertEqual(got.status_code, 200)
            listed = {e["id"] for g in got.json()["groups"] for e in g["entries"]}
            self.assertIn(drunk, listed)
        async with await self.client(self.player) as client:
            refused = await client.get(f"/me/campaigns/{self.cref}/web/cleanup")
            self.assertEqual(refused.status_code, 404)
        async with await self.client(self.session) as client:
            places = await client.get(f"/me/campaigns/{self.cref}/web/cleanup/{drunk}/places")
            self.assertEqual(places.json(), {"places": [], "morePlaces": 0})
        async with await self.client(self.player) as client:
            refused = await client.get(f"/me/campaigns/{self.cref}/web/cleanup/{drunk}/places")
            self.assertEqual(refused.status_code, 404)
