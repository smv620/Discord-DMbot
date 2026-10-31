"""Beliefs that someone lied (#3091, part 2): `suspected_lie` and `known_lie` need to name who lied
and a shape that is false for the believer; the DM sees every lie, a player only what their own
characters think and may know; backups keep them; Undo works. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns import CampaignError
from dmbot.memory.backup import MemorySection
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests import test_moved_known as moved
from tests.test_memory_store import GUILD_A


class Lies(moved.MergeMovesConnections):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.valyra = await self.add("Valyra", type="character", role="npc", status=CONFIRMED)
        self.rel = await self.connect(self.valyra)  # secret: "Valyra knows Captain Vale"
        views = {p.entity_id: p.id for p in await self.memory.perspectives(GUILD_A, self.c)}
        self.truth = next(p.id for p in await self.memory.perspectives(GUILD_A, self.c)
                          if p.kind == "truth")  # fmt: skip
        self.views = views

    async def pc_view(self) -> str:
        await self.memory.set_connection_knowers(GUILD_A, self.c, self.rel, [self.pc], source="dm")
        found = await self.memory.perspectives(GUILD_A, self.c)
        return next(p.id for p in found if p.entity_id == self.pc)

    async def think(
        self, view: str, status: str, confidence: float, sources: list[str], **kw: Any
    ) -> Any:
        return await self.memory.believe(
            GUILD_A, self.c, view, self.rel, confidence, status, source="dm", sources=sources, **kw
        )


class WritingALie(Lies):
    async def test_a_known_lie_is_false_and_names_who_lied(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "known_lie", 0.0, [self.valyra])
        self.assertEqual(made.value.status, "known_lie")
        for confidence in (0.5, 1.0):
            with self.subTest(confidence=confidence), self.assertRaises(MemoryRuleError):
                await self.think(view, "known_lie", confidence, [self.valyra])

    async def test_a_suspected_lie_is_between_sure_and_not_sure(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "suspected_lie", 0.5, [self.valyra])
        self.assertEqual(made.value.confidence, 0.5)
        for confidence in (0.0, 1.0):
            with self.subTest(confidence=confidence), self.assertRaises(MemoryRuleError):
                await self.think(view, "suspected_lie", confidence, [self.valyra])

    async def test_it_must_name_a_character_of_this_campaign(self) -> None:
        view = await self.pc_view()
        keep = await self.add("The Keep", type="place", status=CONFIRMED)
        for sources in ([], [keep], ["f" * 32]):
            with self.subTest(sources=sources), self.assertRaises(MemoryRuleError):
                await self.think(view, "suspected_lie", 0.5, sources)

    async def test_the_truth_confirms_a_lie_and_never_suspects_one(self) -> None:
        await self.think(self.truth, "known_lie", 0.0, [self.valyra])
        with self.assertRaises(MemoryRuleError):
            await self.think(self.truth, "suspected_lie", 0.5, [self.valyra])

    async def test_the_party_as_a_whole_cannot_hold_a_lie(self) -> None:
        party = next(
            p.id for p in await self.memory.perspectives(GUILD_A, self.c) if p.kind == "party"
        )
        with self.assertRaises(MemoryRuleError):
            await self.think(party, "suspected_lie", 0.5, [self.valyra])

    async def test_a_lie_can_be_undone(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "suspected_lie", 0.5, [self.valyra])
        assert made.batch is not None
        await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        self.assertEqual(await self.memory.lies(GUILD_A, self.c, source="dm"), [])


class ReadingLies(Lies):
    async def test_the_dm_sees_every_lie(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        await self.think(self.truth, "known_lie", 0.0, [self.valyra])
        got = await self.memory.lies(GUILD_A, self.c, source="dm")
        self.assertEqual(
            sorted((lie.believer, lie.liar, lie.status) for lie in got if lie.believer is not None),
            [(self.pc, self.valyra, "suspected_lie")],
        )
        self.assertEqual([lie.status for lie in got if lie.believer is None], ["known_lie"])

    async def test_only_the_dm_asks_for_every_lie(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.lies(GUILD_A, self.c, source="extractor")

    async def test_a_player_sees_what_their_own_characters_think(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        mine = await self.memory.lies_my_characters_think(GUILD_A, self.c, 1)
        self.assertEqual([(lie.believer, lie.liar, lie.status) for lie in mine],
                         [(self.pc, self.valyra, "suspected_lie")])  # fmt: skip

    async def test_a_player_never_sees_another_characters_or_the_dms_view(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        await self.think(self.truth, "known_lie", 0.0, [self.valyra])
        other_player = 999  # plays no character here
        self.assertEqual(
            await self.memory.lies_my_characters_think(GUILD_A, self.c, other_player), []
        )

    async def test_a_lie_about_a_liar_the_player_may_not_know_is_not_shown(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.valyra, True, source="dm")
        self.assertEqual(await self.memory.lies_my_characters_think(GUILD_A, self.c, 1), [])

    async def test_a_lie_about_a_connection_the_player_may_not_know_is_not_shown(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        await self.memory.set_connection_knowers(GUILD_A, self.c, self.rel, [], source="dm")
        self.assertEqual(await self.memory.lies_my_characters_think(GUILD_A, self.c, 1), [])


class Backups(Lies):
    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD_A) as conn:
            return await MemorySection().dump(conn, GUILD_A, self.c)

    async def test_a_backup_keeps_a_lie_and_who_lied(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        rows = await self.rows()
        copy = (await self.campaigns.create(GUILD_A, "Copy", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await MemorySection().load(conn, GUILD_A, copy, rows)
        kept = await self.memory.lies(GUILD_A, copy, source="dm")
        self.assertEqual([(lie.liar, lie.status) for lie in kept], [(self.valyra, "suspected_lie")])

    async def test_a_lie_without_a_source_in_a_file_is_refused_as_damaged(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        rows = [
            r
            for r in await self.rows()
            if not (r.get("table") == "evidence" and r["kind"] == "source")
        ]
        copy = (await self.campaigns.create(GUILD_A, "Damaged", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            with self.assertRaises(CampaignError):
                await MemorySection().load(conn, GUILD_A, copy, rows)
