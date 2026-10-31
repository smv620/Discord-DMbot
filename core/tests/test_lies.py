"""Beliefs that someone lied (#3091, part 2): `suspected_lie` and `known_lie` need to name who lied
and a shape that is false for the believer; the DM sees every lie, a player only what their own
characters think and may know; backups keep them; Undo works. Invented names."""

from __future__ import annotations

from typing import Any

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

    async def test_a_suspected_lie_is_above_nothing_and_at_most_half(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "suspected_lie", 0.5, [self.valyra])
        self.assertEqual(made.value.confidence, 0.5)
        for confidence in (0.0, 0.51, 1.0):
            with self.subTest(confidence=confidence), self.assertRaises(MemoryRuleError) as why:
                await self.think(view, "suspected_lie", confidence, [self.valyra])
            self.assertEqual(
                str(why.exception),
                "A lie is believed false: a sure lie is 0%, a suspected one is above 0% and at "
                "most 50%.",
            )

    async def test_it_must_name_a_character_of_this_campaign(self) -> None:
        view = await self.pc_view()
        keep = await self.add("The Keep", type="place", status=CONFIRMED)
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", 5)).id
        stranger = await self.add("Pike", campaign=elsewhere, type="character", status=CONFIRMED)
        for sources in ([], [keep], ["f" * 32], [stranger]):
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


class MoreRules(Lies):
    async def test_a_pinned_connection_takes_no_lie_and_nothing_below_a_hundred_on_the_truth(
        self,
    ) -> None:
        await self.memory.update_relation(GUILD_A, self.c, self.rel, source="dm", secret=False)
        await self.memory.pin(GUILD_A, self.c, self.rel, True, source="dm")
        for status, confidence, sources in (
            ("known_lie", 0.0, [self.valyra]),
            ("false_evidence", 0.0, []),
            ("theory", 0.5, []),
        ):
            with self.subTest(status=status), self.assertRaises(MemoryRuleError) as why:
                await self.think(self.truth, status, confidence, sources)
            self.assertIn("Unpin it first", str(why.exception))
        await self.memory.pin(GUILD_A, self.c, self.rel, False, source="dm")
        done = await self.think(self.truth, "known_lie", 0.0, [self.valyra])
        self.assertEqual(done.value.status, "known_lie")

    async def test_an_npcs_point_of_view_can_hold_a_lie(self) -> None:
        await self.memory.set_connection_knowers(GUILD_A, self.c, self.rel, [self.pc], source="dm")
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", self.rel, {self.captain: 1.0}, source="dm"
        )
        view = next(
            p.id for p in await self.memory.perspectives(GUILD_A, self.c)
            if p.entity_id == self.captain
        )  # fmt: skip
        made = await self.think(view, "known_lie", 0.0, [self.valyra])
        self.assertEqual(made.value.status, "known_lie")

    async def test_a_reworded_connection_keeps_the_lie_and_who_lied(self) -> None:
        view = await self.pc_view()
        await self.memory.update_relation(
            GUILD_A, self.c, self.rel, source="dm", status="confirmed"
        )
        await self.think(view, "suspected_lie", 0.4, [self.valyra])
        await self.memory.change_relation_words(
            GUILD_A, self.c, self.rel, "enemy_of", "", source="dm"
        )
        lies = await self.memory.lies(GUILD_A, self.c, source="dm")
        self.assertEqual([(lie.liar, lie.status) for lie in lies], [(self.valyra, "suspected_lie")])
        self.assertNotEqual(lies[0].relation_id, self.rel)  # (on the new connection)


class UndoAndRedo(Lies):
    async def test_a_lie_undone_and_redone_still_names_who_lied(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "known_lie", 0.0, [self.valyra])
        assert made.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        self.assertEqual(await self.memory.lies(GUILD_A, self.c, source="dm"), [])
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")  # redo
        got = await self.memory.lies(GUILD_A, self.c, source="dm")
        self.assertEqual([(lie.liar, lie.status) for lie in got], [(self.valyra, "known_lie")])

    async def test_a_campaign_that_went_through_it_still_backs_up_and_loads(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "known_lie", 0.0, [self.valyra])
        assert made.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        assert undone.batch is not None
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")
        async with self.db.guild(GUILD_A) as conn:
            rows = await MemorySection().dump(conn, GUILD_A, self.c)
        copy = (await self.campaigns.create(GUILD_A, "Copy", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await MemorySection().load(conn, GUILD_A, copy, rows)
        kept = await self.memory.lies(GUILD_A, copy, source="dm")
        self.assertEqual([lie.status for lie in kept], ["known_lie"])
        self.assertIsNotNone(kept[0].liar)  # the backup keeps who lied, as the live reader does

    async def test_a_redo_after_the_liar_is_merged_names_the_kept_character(self) -> None:
        view = await self.pc_view()
        made = await self.think(view, "known_lie", 0.0, [self.valyra])
        assert made.batch is not None
        undone = await self.memory.undo(GUILD_A, self.c, made.batch, source="dm")
        assert undone.batch is not None
        kept = await self.add("Corin", type="character", role="npc", status=CONFIRMED)
        await self.memory.merge(GUILD_A, self.c, kept, self.valyra, source="dm", dm_said_same=True)
        await self.memory.undo(GUILD_A, self.c, undone.batch, source="dm")
        got = await self.memory.lies(GUILD_A, self.c, source="dm")
        self.assertEqual([lie.liar for lie in got], [kept])

    async def test_a_lie_with_no_source_link_is_still_read_with_no_liar(self) -> None:
        await self.think(self.truth, "known_lie", 0.0, [self.valyra])
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_evidence WHERE guild_id = %s AND campaign_id = %s"
                " AND kind = 'source'",
                (GUILD_A, self.c),
            )
        got = await self.memory.lies(GUILD_A, self.c, source="dm")
        self.assertEqual([(lie.believer, lie.liar) for lie in got], [(None, None)])


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

    async def test_a_lie_without_a_liar_in_a_file_loads_with_a_placeholder(self) -> None:
        view = await self.pc_view()
        await self.think(view, "suspected_lie", 0.5, [self.valyra])
        rows = [
            r
            for r in await self.rows()
            if not (r.get("table") == "evidence" and r["kind"] == "source")
        ]
        copy = (await self.campaigns.create(GUILD_A, "Lost", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await MemorySection().load(conn, GUILD_A, copy, rows)
        kept = await self.memory.lies(GUILD_A, copy, source="dm")
        self.assertEqual([(lie.liar, lie.status) for lie in kept], [(None, "suspected_lie")])
