"""Why a belief changed (#3091, part 1): `derived_from` and `change_kind`, the `source` evidence
kind, the DM's finder for what rests on a source, and what survives erasure. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import evidence
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from tests.test_memory_store import GUILD_A, MemoryTest


class Lineage(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.morn = await self.add("Advisor Morn", type="character", role="npc", status=CONFIRMED)
        self.cerric = await self.add("Cerric", type="character", role="npc", status=CONFIRMED)
        self.tran = await self.add("Tran", type="character", role="npc", status=CONFIRMED)
        self.bruno = await self.add("Bruno", type="character", role="npc", status=CONFIRMED)
        self.rel = (await self.relate(self.morn, "knows", self.bruno)).value[0].id
        self.other = (await self.relate(self.morn, "knows", self.tran)).value[0].id
        self.truth = await self.view_of(kind="truth")
        self.cerric_view = await self.view_for(self.cerric)
        self.tran_view = await self.view_for(self.tran)

    async def view_of(self, *, kind: str) -> str:
        found = await self.memory.perspectives(GUILD_A, self.c)
        return next(p.id for p in found if p.kind == kind)

    async def view_for(self, entity: str) -> str:
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, "connection", self.rel, {entity: 1.0}, source="dm"
        )
        found = await self.memory.perspectives(GUILD_A, self.c)
        return next(p.id for p in found if p.entity_id == entity)

    async def believe(
        self, view: str, rel: str, confidence: float = 0.5, status: str = "theory", **kw: Any
    ) -> Any:
        return await self.memory.believe(
            GUILD_A, self.c, view, rel, confidence, status, source="dm", **kw
        )

    async def row(self, belief_id: str) -> dict[str, Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT derived_from, change_kind FROM memory_beliefs"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.c, belief_id),
            )
            found = await cur.fetchone()
            assert found is not None
            return dict(found)


class TheChain(Lineage):
    async def test_a_belief_with_no_lineage_has_none(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel)).value
        self.assertEqual(await self.row(first.id), {"derived_from": None, "change_kind": None})

    async def test_an_update_revises_the_same_views_belief_in_the_same_connection(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel, 0.4)).value
        second = (
            await self.believe(
                self.cerric_view, self.rel, 0.8, derived_from=first.id, change_kind="update"
            )
        ).value
        self.assertEqual(second.derived_from, first.id)
        self.assertEqual(second.change_kind, "update")
        self.assertEqual(
            await self.row(second.id), {"derived_from": first.id, "change_kind": "update"}
        )

    async def test_a_retraction_is_no_longer_held_as_fact(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel, 1.0, "revealed_fact")).value
        with self.assertRaises(MemoryRuleError):
            await self.believe(
                self.cerric_view, self.rel, 1.0, derived_from=first.id, change_kind="retraction"
            )
        done = (
            await self.believe(
                self.cerric_view, self.rel, 0.0, "false_evidence",
                derived_from=first.id, change_kind="retraction",
            )
        ).value  # fmt: skip
        self.assertEqual(done.change_kind, "retraction")

    async def test_a_branch_is_a_competing_theory_on_another_connection(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel, 0.6)).value
        with self.assertRaises(MemoryRuleError):  # the same connection is an update, not a branch
            await self.believe(
                self.cerric_view, self.rel, 0.3, derived_from=first.id, change_kind="branch"
            )
        branch = (
            await self.believe(
                self.cerric_view, self.other, 0.3, derived_from=first.id, change_kind="branch"
            )
        ).value
        self.assertEqual(branch.change_kind, "branch")
        self.assertEqual(await self.row(first.id), {"derived_from": None, "change_kind": None})

    async def test_told_passes_the_same_connection_on_from_another_view(self) -> None:
        bruno_view = await self.view_for(self.bruno)
        said = (await self.believe(bruno_view, self.rel, 1.0, "revealed_fact")).value
        with self.assertRaises(MemoryRuleError):  # the same point of view is not "told"
            await self.believe(bruno_view, self.rel, 0.9, derived_from=said.id, change_kind="told")
        told = (
            await self.believe(
                self.tran_view, self.rel, 0.7, derived_from=said.id, change_kind="told"
            )
        ).value
        self.assertEqual(told.derived_from, said.id)

    async def test_a_kind_without_a_belief_or_a_belief_without_a_kind_is_refused(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel)).value
        for kw in (
            {"derived_from": first.id},
            {"change_kind": "update"},
            {"derived_from": first.id, "change_kind": "gossip"},
            {"derived_from": "f" * 32, "change_kind": "update"},
        ):
            with self.subTest(kw=kw), self.assertRaises(MemoryRuleError):
                await self.believe(self.cerric_view, self.rel, 0.7, **kw)


class Sources(Lineage):
    async def test_a_source_is_an_entry_of_this_campaign(self) -> None:
        made = (await self.believe(self.cerric_view, self.rel, sources=[self.morn])).value
        links = await self.links(made.id)
        self.assertEqual([(k, t) for k, t in links], [("source", self.morn)])
        with self.assertRaises(MemoryRuleError):
            await self.believe(self.cerric_view, self.rel, 0.6, sources=["e" * 32])

    async def links(self, belief_id: str) -> list[tuple[str, str | None]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT kind, target_id FROM memory_evidence WHERE guild_id = %s"
                " AND campaign_id = %s AND belief_id = %s ORDER BY kind, target_id",
                (GUILD_A, self.c, belief_id),
            )
            return [(r["kind"], r["target_id"]) for r in await cur.fetchall()]

    async def test_a_merge_moves_the_source_to_the_kept_entry(self) -> None:
        twin = await self.add("Morn the Advisor", type="character", role="npc", status=CONFIRMED)
        made = (await self.believe(self.cerric_view, self.rel, sources=[twin])).value
        await self.memory.merge(GUILD_A, self.c, self.morn, twin, source="dm", dm_said_same=True)
        self.assertEqual(await self.links(made.id), [("source", self.morn)])

    async def test_the_words_for_a_source_placeholder_have_no_name(self) -> None:
        link = evidence.Link("1" * 32, "source", None, 5, 1)
        self.assertEqual(
            evidence.words_for(link, 5), "someone who said it from session 5, no longer available"
        )


class TheFinder(Lineage):
    async def test_it_finds_exactly_the_beliefs_resting_on_one_source(self) -> None:
        said = (await self.believe(self.cerric_view, self.rel, 0.9, sources=[self.morn])).value
        second = (
            await self.believe(
                self.cerric_view, self.rel, 0.4, derived_from=said.id, change_kind="update"
            )
        ).value
        told = (
            await self.believe(
                self.tran_view, self.rel, 0.5, derived_from=second.id, change_kind="told"
            )
        ).value
        await self.believe(self.truth, self.other, 0.5)  # unrelated: cites nobody
        found = await self.memory.what_rests_on(GUILD_A, self.c, source="dm", entity_id=self.morn)
        # the superseded first belief is not current; its revision and the telling are
        self.assertEqual({r.belief_id for r in found}, {second.id, told.id})
        self.assertEqual({r.belief_id: r.depth for r in found}, {second.id: 1, told.id: 2})

    async def test_it_finds_what_came_from_a_belief_but_not_the_belief_itself(self) -> None:
        start = (await self.believe(self.cerric_view, self.rel, 0.9)).value
        child = (
            await self.believe(
                self.cerric_view, self.other, 0.3, derived_from=start.id, change_kind="branch"
            )
        ).value
        found = await self.memory.what_rests_on(GUILD_A, self.c, source="dm", belief_id=start.id)
        self.assertEqual([r.belief_id for r in found], [child.id])

    async def test_only_the_dm_asks_and_it_asks_one_thing(self) -> None:
        with self.assertRaises(MemoryRuleError):
            await self.memory.what_rests_on(
                GUILD_A, self.c, source="extractor", entity_id=self.morn
            )
        with self.assertRaises(ValueError):
            await self.memory.what_rests_on(GUILD_A, self.c, source="dm")

    async def test_a_loop_in_the_chain_ends(self) -> None:
        a = (await self.believe(self.cerric_view, self.rel, 0.5, sources=[self.morn])).value
        async with self.db.guild(GUILD_A) as conn:  # (made by hand: the writer never allows it)
            await conn.execute(
                "UPDATE memory_beliefs SET derived_from = %s, change_kind = 'update'"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (a.id, GUILD_A, self.c, a.id),
            )
        found = await self.memory.what_rests_on(GUILD_A, self.c, source="dm", entity_id=self.morn)
        self.assertEqual([r.belief_id for r in found], [a.id])

    async def test_nothing_crosses_campaigns(self) -> None:
        await self.believe(self.cerric_view, self.rel, 0.9, sources=[self.morn])
        elsewhere = (await self.campaigns.create(GUILD_A, "Elsewhere", 5)).id
        found = await self.memory.what_rests_on(
            GUILD_A, elsewhere, source="dm", entity_id=self.morn
        )
        self.assertEqual(found, [])
        first = (await self.believe(self.cerric_view, self.rel)).value
        one = await self.add("Wren", campaign=elsewhere, type="character", status=CONFIRMED)
        two = await self.add("Pike", campaign=elsewhere, type="character", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, elsewhere, one, "knows", two, source="dm", confidence=1.0
        )
        rel = made.value[0].id
        found = await self.memory.perspectives(GUILD_A, elsewhere)
        truth = next(p.id for p in found if p.kind == "truth")
        # (the same write is fine without the lineage: only the other campaign's belief is refused)
        await self.memory.believe(GUILD_A, elsewhere, truth, rel, 0.5, "theory", source="dm")
        with self.assertRaises(MemoryRuleError):
            await self.memory.believe(
                GUILD_A, elsewhere, truth, rel, 0.6, "theory", source="dm",
                derived_from=first.id, change_kind="update",
            )  # fmt: skip


class Erasure(Lineage):
    async def test_a_deleted_earlier_belief_leaves_a_placeholder_that_holds_no_words(self) -> None:
        first = (await self.believe(self.cerric_view, self.other, 0.9)).value
        child = (
            await self.believe(
                self.cerric_view, self.rel, 0.3, derived_from=first.id, change_kind="branch"
            )
        ).value
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "DELETE FROM memory_beliefs WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (GUILD_A, self.c, first.id),
            )
        self.assertEqual(await self.row(child.id), {"derived_from": None, "change_kind": "branch"})


class Backup(Lineage):
    async def test_a_backup_keeps_lineage_and_sources_and_an_old_file_still_loads(self) -> None:
        from dmbot.memory.backup import MemorySection

        first = (await self.believe(self.cerric_view, self.rel, 0.9, sources=[self.morn])).value
        second = (
            await self.believe(
                self.cerric_view, self.rel, 0.4, derived_from=first.id, change_kind="update"
            )
        ).value
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        copy = (await self.campaigns.create(GUILD_A, "Copy", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, rows)
            cur = await conn.execute(
                "SELECT id, derived_from, change_kind FROM memory_beliefs WHERE campaign_id = %s"
                " AND derived_from IS NOT NULL",
                (copy,),
            )
            kept = [dict(r) for r in await cur.fetchall()]
            cur = await conn.execute(
                "SELECT kind, target_id FROM memory_evidence WHERE campaign_id = %s"
                " AND kind = 'source'",
                (copy,),
            )
            sources = [dict(r) for r in await cur.fetchall()]
        self.assertEqual(
            kept, [{"id": second.id, "derived_from": first.id, "change_kind": "update"}]
        )
        self.assertEqual(sources, [{"kind": "source", "target_id": self.morn}])
        # a file from before lineage carries neither column and still loads
        old = [
            {k: v for k, v in r.items() if k not in ("derived_from", "change_kind")}
            if r.get("table") == "belief"
            else r
            for r in rows
        ]
        again = (await self.campaigns.create(GUILD_A, "Again", 8)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, again, old)

    async def test_a_link_to_a_belief_the_file_does_not_hold_becomes_a_placeholder(self) -> None:
        from dmbot.memory.backup import MemorySection

        first = (await self.believe(self.cerric_view, self.rel, 0.9)).value
        await self.believe(
            self.cerric_view, self.rel, 0.4, derived_from=first.id, change_kind="update"
        )
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        trimmed = [r for r in rows if not (r.get("table") == "belief" and r["id"] == first.id)]
        copy = (await self.campaigns.create(GUILD_A, "Trimmed", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, trimmed)
            cur = await conn.execute(
                "SELECT derived_from, change_kind FROM memory_beliefs WHERE campaign_id = %s"
                " AND change_kind IS NOT NULL",
                (copy,),
            )
            self.assertEqual(
                [dict(r) for r in await cur.fetchall()],
                [{"derived_from": None, "change_kind": "update"}],
            )


class WhenWordsAreRemoved(Lineage):
    async def test_lineage_survives_a_removed_line_and_holds_no_words(self) -> None:
        first = (await self.believe(self.cerric_view, self.rel, 0.9, sources=[self.morn])).value
        second = (
            await self.believe(
                self.cerric_view, self.rel, 0.4, derived_from=first.id, change_kind="update"
            )
        ).value
        async with self.db.guild(GUILD_A) as conn:
            await evidence.placeholder_lines(conn, GUILD_A, ["s1:1000:7"])
        self.assertEqual(
            await self.row(second.id), {"derived_from": first.id, "change_kind": "update"}
        )
        found = await self.memory.what_rests_on(GUILD_A, self.c, source="dm", entity_id=self.morn)
        self.assertEqual([r.belief_id for r in found], [second.id])
