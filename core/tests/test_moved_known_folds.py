"""Supervisor's probes for PR #3080 (#3073), as tests: a plain merge closes onto an end the players
are not shown, a join keeps, a twin fold decides by the ends alone and carries every belief but the
player characters' onto a twin they were not shown. Invented names."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory.gate import CONNECTION
from dmbot.memory.models import CONFIRMED, new_id
from dmbot.memory.store import MemoryStore
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A
from tests.test_same_person_join import JoinBase


class Common:
    # What the test bases (`Helpers`, `JoinBase`) give; stated here so the helpers below type-check.
    memory: MemoryStore
    db: Database
    c: str
    ids: dict[str, str]

    async def undo(self, written: Any) -> None:
        assert written.batch is not None
        await self.memory.undo(GUILD_A, self.c, written.batch, source="dm")

    async def kn(self, rel: str) -> list[str]:
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        names = {v: k for k, v in self.ids.items()}
        return sorted(names.get(p, "other") for p in got.get(rel, []))

    async def bel(self, rel: str) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for name in ("okthandom", "mara"):
            pc = self.ids[name]
            async with self.db.guild(GUILD_A) as conn:
                cur = await conn.execute(
                    "SELECT b.confidence FROM memory_beliefs b JOIN memory_perspectives p"
                    " ON p.guild_id = b.guild_id AND p.campaign_id = b.campaign_id"
                    " AND p.id = b.perspective_id WHERE b.guild_id = %s AND b.campaign_id = %s"
                    " AND b.relation_id = %s AND p.entity_id = %s ORDER BY b.seq DESC LIMIT 1",
                    (GUILD_A, self.c, rel, pc),
                )
                row = await cur.fetchone()
            out[name] = None if row is None else float(row["confidence"])
        return out

    async def rel(self, a: str, b: str, **kw: Any) -> str:
        kw.setdefault("status", CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A,
            self.c,
            a,
            "knows",
            b,
            source="dm",
            confidence=1.0,
            **kw,
        )
        return str(made.value[0].id)

    async def pv(self, name: str) -> str:
        views = {p.entity_id: p.id for p in await self.memory.perspectives(GUILD_A, self.c)}
        return views[self.ids[name]]


async def limit(t: Helpers, eid: str) -> None:
    async with t.db.guild(GUILD_A) as conn:
        await conn.execute(
            "UPDATE memory_entities SET audience_limited = true"
            " WHERE guild_id=%s AND campaign_id=%s AND id=%s",
            (GUILD_A, t.c, eid),
        )


class Merge(Common, Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        self.vale = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        self.a = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        self.hidden = await self.add(
            "Brin", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )

    async def test_p1_hidden_closes_rows_and_beliefs_undo_restores(self) -> None:
        pub = await self.rel(self.a, self.vale)
        sec = await self.rel(self.a, self.vale, secret=True, detail="x")
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, sec, [self.ids["okthandom"]], source="dm"
        )
        await self.memory.believe(
            GUILD_A, self.c, await self.pv("mara"), pub, 1.0, "revealed_fact", source="dm"
        )
        before = (await self.kn(pub), await self.bel(pub), await self.kn(sec), await self.bel(sec))
        w = await self.memory.merge(
            GUILD_A, self.c, self.hidden, self.a, source="dm", dm_said_same=True
        )
        after = (await self.kn(pub), await self.bel(pub), await self.kn(sec), await self.bel(sec))
        await self.undo(w)
        undo = (await self.kn(pub), await self.bel(pub), await self.kn(sec), await self.bel(sec))
        self.assertEqual((after[0], after[2]), ([], []))
        self.assertTrue(all((v or 0) < 1.0 for v in after[1].values()))
        self.assertTrue(all((v or 0) < 1.0 for v in after[3].values()))
        self.assertEqual(undo, before)

    async def test_p1b_limited_closes(self) -> None:
        b = await self.add("Cora", type="character", role="npc", status=CONFIRMED)
        await limit(self, b)
        pub = await self.rel(self.a, self.vale)
        await self.memory.merge(GUILD_A, self.c, b, self.a, source="dm", dm_said_same=True)
        self.assertEqual(await self.kn(pub), [])

    async def test_p1c_merge_many_hidden_closes(self) -> None:
        pub = await self.rel(self.a, self.vale)
        w = await self.memory.merge_many(GUILD_A, self.c, self.hidden, [self.a], source="dm")
        self.assertEqual(await self.kn(pub), [])
        await self.undo(w)
        self.assertEqual(await self.kn(pub), ["mara", "okthandom"])

    async def test_p2_shown_keeps(self) -> None:
        b = await self.add("Arlo the Bold", type="character", role="npc", status=CONFIRMED)
        pub = await self.rel(self.a, self.vale)
        sec = await self.rel(self.a, self.vale, secret=True, detail="x")
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, sec, [self.ids["okthandom"]], source="dm"
        )
        before = (await self.kn(pub), await self.kn(sec), await self.bel(sec))
        await self.memory.merge(GUILD_A, self.c, b, self.a, source="dm", dm_said_same=True)
        self.assertEqual((await self.kn(pub), await self.kn(sec), await self.bel(sec)), before)

    async def test_p3_a_secret_twin_fold_onto_hidden_carries_no_player_belief(self) -> None:
        twin = await self.rel(self.hidden, self.vale, secret=True)
        mine = await self.rel(self.a, self.vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.ids["okthandom"]], source="dm"
        )
        before = (
            await self.kn(mine),
            await self.bel(mine),
            await self.kn(twin),
            await self.bel(twin),
        )
        w = await self.memory.merge(
            GUILD_A, self.c, self.hidden, self.a, source="dm", dm_said_same=True
        )
        self.assertEqual(await self.kn(twin), [])
        self.assertTrue(all((v or 0) < 1.0 for v in (await self.bel(twin)).values()))
        mine_now = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        self.assertEqual([c.subject for c in mine_now.connections], [])
        await self.undo(w)
        undo = (
            await self.kn(mine),
            await self.bel(mine),
            await self.kn(twin),
            await self.bel(twin),
        )
        self.assertEqual(undo, before)

    async def test_p3b_a_public_twin_fold_onto_hidden_carries_no_player_belief(self) -> None:
        twin = await self.rel(self.hidden, self.vale)
        mine = await self.rel(self.a, self.vale)
        await self.memory.believe(
            GUILD_A, self.c, await self.pv("mara"), mine, 1.0, "revealed_fact", source="dm"
        )
        await self.memory.merge(
            GUILD_A, self.c, self.hidden, self.a, source="dm", dm_said_same=True
        )
        self.assertTrue(all((v or 0) < 1.0 for v in (await self.bel(twin)).values()))

    async def test_p4_a_secret_fold_between_shown_entries_keeps_the_dms_list(self) -> None:
        b = await self.add("Arlo the Bold", type="character", role="npc", status=CONFIRMED)
        twin = await self.rel(b, self.vale, secret=True)
        mine = await self.rel(self.a, self.vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.ids["okthandom"]], source="dm"
        )
        w = await self.memory.merge(GUILD_A, self.c, b, self.a, source="dm", dm_said_same=True)
        self.assertEqual(await self.kn(twin), ["okthandom"])
        await self.undo(w)
        self.assertEqual(await self.kn(mine), ["okthandom"])
        self.assertEqual(await self.kn(twin), [])

    async def test_p5_a_limited_entrys_list_is_kept_when_it_merges_into_a_hidden_one(self) -> None:
        a2 = await self.add("Cora", type="character", role="npc", status=CONFIRMED)
        await limit(self, a2)
        sec = await self.rel(a2, self.vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, sec, [self.ids["okthandom"]], source="dm"
        )
        await self.memory.merge(GUILD_A, self.c, self.hidden, a2, source="dm", dm_said_same=True)
        self.assertEqual(await self.kn(sec), ["okthandom"])

    async def test_x1_an_npcs_belief_is_carried_onto_a_hidden_twin(self) -> None:
        twin = await self.rel(self.hidden, self.vale)
        mine = await self.rel(self.a, self.vale)
        npc = await self.npc_view(self.vale)
        await self.memory.believe(GUILD_A, self.c, npc, mine, 0.7, "theory", source="dm")
        before = await self.belief_of(npc, mine)
        await self.memory.merge(
            GUILD_A, self.c, self.hidden, self.a, source="dm", dm_said_same=True
        )
        self.assertEqual(await self.belief_of(npc, twin), before)

    async def test_x2_the_dms_pin_is_carried_onto_a_hidden_twin(self) -> None:
        twin = await self.rel(self.hidden, self.vale)
        mine = await self.rel(self.a, self.vale)
        await self.memory.pin(GUILD_A, self.c, mine, True, source="dm")
        await self.memory.merge(
            GUILD_A, self.c, self.hidden, self.a, source="dm", dm_said_same=True
        )
        self.assertIn(twin, await self.memory.pinned_connections(GUILD_A, self.c))

    async def test_x3_a_fold_between_hidden_entries_keeps_the_dms_list(self) -> None:
        a2 = await self.add(
            "Cora", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )
        twin = await self.rel(self.hidden, self.vale, secret=True)
        mine = await self.rel(a2, self.vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.ids["okthandom"]], source="dm"
        )
        await self.memory.merge(GUILD_A, self.c, self.hidden, a2, source="dm", dm_said_same=True)
        self.assertEqual(await self.kn(twin), ["okthandom"])

    async def test_x4_a_public_fold_between_shown_entries_and_its_undo(self) -> None:
        b = await self.add("Arlo the Bold", type="character", role="npc", status=CONFIRMED)
        twin = await self.rel(b, self.vale)
        mine = await self.rel(self.a, self.vale)
        before = (await self.kn(mine), await self.kn(twin))
        w = await self.memory.merge(GUILD_A, self.c, b, self.a, source="dm", dm_said_same=True)
        await self.undo(w)
        self.assertEqual((await self.kn(mine), await self.kn(twin)), before)

    async def npc_view(self, entity: str) -> str:
        pid = new_id()
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_perspectives (guild_id, campaign_id, id, kind, entity_id,"
                " created_at) VALUES (%s,%s,%s,'npc',%s,0)",
                (GUILD_A, self.c, pid, entity),
            )
        return pid

    async def belief_of(self, pid: str, rel: str) -> tuple[float, bool] | None:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT confidence, pinned FROM memory_beliefs WHERE guild_id=%s AND campaign_id=%s"
                " AND relation_id=%s AND perspective_id=%s ORDER BY seq DESC LIMIT 1",
                (GUILD_A, self.c, rel, pid),
            )
            row = await cur.fetchone()
        return None if row is None else (float(row["confidence"]), bool(row["pinned"]))


class Join(Common, JoinBase):
    async def test_j1_join_onto_hidden_ken_keeps_guard_knower(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        vale = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        sec = await self.rel(self.guard, vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, sec, [self.sees_guard], source="dm"
        )
        before = (await self.kn(sec), await self.bel(sec))
        w = await self.join()
        after = (await self.kn(sec), await self.bel(sec))
        await self.undo(w)
        self.assertEqual(before, after)
        self.assertEqual(before, (await self.kn(sec), await self.bel(sec)))

    async def test_j2_a_twin_fold_moves_the_knowers_to_a_hidden_twin(self) -> None:
        await self.memory.set_hidden_from_players(GUILD_A, self.c, self.ken, True, source="dm")
        vale = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        twin = await self.rel(self.ken, vale, secret=True)
        mine = await self.rel(self.guard, vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.sees_guard], source="dm"
        )
        w = await self.join()
        self.assertEqual(await self.kn(twin), ["okthandom"])
        await self.undo(w)
        self.assertEqual(await self.kn(mine), ["okthandom"])
        self.assertEqual(await self.kn(twin), [])

    async def test_j3_a_twin_fold_into_a_public_twin_keeps_its_flags(self) -> None:
        vale = await self.add("Captain Vale", type="character", role="npc", status=CONFIRMED)
        twin = await self.rel(self.ken, vale)
        mine = await self.rel(self.guard, vale, secret=True)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, mine, [self.sees_guard], source="dm"
        )
        await self.join()
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT secret FROM memory_relations WHERE guild_id=%s AND campaign_id=%s"
                " AND id=%s",
                (GUILD_A, self.c, twin),
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertFalse(row["secret"])
        self.assertIn("okthandom", await self.kn(twin))
