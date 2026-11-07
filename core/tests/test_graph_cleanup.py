"""The graph clean-up's door, count and scene sweep (#3740), on Postgres: the DM gets the isolated
groups, anyone else nothing; the Discord count agrees with the page; and an isolated entry keeps
its scene until it is linked. Invented names."""

from __future__ import annotations

from dmbot.memory.models import DM as DM_SOURCE
from dmbot.memory.scenes import ENTITY, Scene, Scenes
from dmbot.web import graph_cleanup
from dmbot.web.recaps import RecapError
from tests.test_web_all_names import MEMBER
from tests.test_web_api import ALICE, THURSDAY
from tests.test_web_relationships import BOB_USER, TheRoute, Web


class TheCleanup(Web):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        # (the fixture links Mira to Rook, a player character; the drunk and the sailors are
        # linked only to each other)
        self.drunk = await self.add("Old Fenn", "character", role="npc")
        self.sailors = await self.add("The Salt Crew", "faction")
        await self.link("knows", self.drunk, self.sailors, "drunk-sailors")

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
