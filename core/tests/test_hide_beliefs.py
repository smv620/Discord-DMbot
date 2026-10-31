"""Hiding an entry closes the player characters' rows AND beliefs on what hangs off it, and showing
it again puts back exactly what that hide closed and nothing else (#3082); a connection a join
moved, and the secret name it was moved through, are known through the name, so hiding the entry
they now hang on does not close them (#3037). Invented names."""

from __future__ import annotations

from typing import Any

import psycopg

from dmbot.memory.gate import CONNECTION
from dmbot.memory.models import CONFIRMED
from dmbot.memory.models import DM as DM_SOURCE
from tests.test_memory_store import GUILD_A
from tests.test_moved_known import MergeMovesConnections
from tests.test_recap_web import BOB
from tests.test_web_my_characters_view import MyCharacters


class Base(MergeMovesConnections):
    async def newest(self, rel: str, entity: str | None) -> Any:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT b.confidence, b.status, b.pinned FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                " WHERE b.guild_id = %s AND b.campaign_id = %s AND b.relation_id = %s"
                " AND p.entity_id IS NOT DISTINCT FROM %s ORDER BY b.seq DESC LIMIT 1",
                (GUILD_A, self.c, rel, entity),
            )
            return await cur.fetchone()

    async def truth(self, rel: str) -> Any:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT b.confidence, b.pinned FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                " WHERE b.guild_id = %s AND b.campaign_id = %s AND b.relation_id = %s"
                " AND p.kind = 'truth' ORDER BY b.seq DESC LIMIT 1",
                (GUILD_A, self.c, rel),
            )
            return await cur.fetchone()

    async def mine(self) -> list[str]:
        known = await self.memory.what_my_characters_know(GUILD_A, self.c, 1)
        return [c.relation_id for c in known.connections]

    async def hide(self, entity: str, hidden: bool = True) -> Any:
        return await self.memory.set_hidden_from_players(
            GUILD_A, self.c, entity, hidden, source="dm"
        )

    async def listed(self, arlo_target: str | None = None) -> str:
        """A secret connection of Arlo, listed to the first player character only."""
        arlo = arlo_target or await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        rel = await self.connect(arlo)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.pc], source="dm")
        self.arlo = arlo
        return rel


class HidingAndShowingAgain(Base):
    async def test_hiding_closes_rows_and_belief_and_the_reader(self) -> None:
        rel = await self.listed()
        self.assertIn(rel, await self.mine())
        await self.hide(self.arlo)
        self.assertEqual(await self.knowers(rel), [])
        self.assertFalse(await self.believes(rel))
        self.assertNotIn(rel, await self.mine())

    async def test_a_dm_list_on_a_secret_connection_comes_back_as_it_was(self) -> None:
        rel = await self.listed()
        before = await self.newest(rel, self.pc)
        await self.hide(self.arlo)
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))
        self.assertEqual(await self.newest(rel, self.pc), before)
        self.assertIn(rel, await self.mine())

    async def test_what_the_dm_changed_in_between_stays(self) -> None:
        rel = await self.listed()
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=False)
        await self.hide(self.arlo)
        persp = {p.entity_id: p.id for p in await self.memory.perspectives(GUILD_A, self.c)}
        await self.memory.believe(
            GUILD_A, self.c, persp[self.pc], rel, 0.0, "false_evidence", source="dm"
        )
        await self.hide(self.arlo, False)
        self.assertEqual((await self.newest(rel, self.pc))["status"], "false_evidence")

    async def test_nothing_comes_back_that_the_hide_did_not_close(self) -> None:
        rel = await self.listed()
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [], source="dm")  # removed
        removed = await self.newest(rel, self.pc)
        await self.memory.update_relation(GUILD_A, self.c, rel, source="dm", secret=False)
        await self.hide(self.arlo)
        await self.hide(self.arlo, False)
        self.assertEqual((await self.newest(rel, self.pc))["confidence"], removed["confidence"])
        self.assertFalse(await self.believes(rel))

    async def test_a_public_connection_is_given_no_belief_by_hiding_and_showing(self) -> None:
        arlo = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        rel = await self.connect(arlo, secret=False)
        await self.hide(arlo)
        await self.hide(arlo, False)
        self.assertFalse(await self.believes(rel))

    async def test_undo_of_the_hide_and_of_the_show_each_work(self) -> None:
        rel = await self.listed()
        hid = await self.hide(self.arlo)
        assert hid.batch is not None
        await self.memory.undo(GUILD_A, self.c, hid.batch, source="dm")
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))
        await self.hide(self.arlo)
        shown = await self.hide(self.arlo, False)
        self.assertTrue(await self.believes(rel))
        assert shown.batch is not None
        await self.memory.undo(GUILD_A, self.c, shown.batch, source="dm")
        self.assertEqual(await self.knowers(rel), [])
        self.assertFalse(await self.believes(rel))

    async def test_only_player_characters_close(self) -> None:
        rel = await self.listed()
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {self.pc: 1.0, self.captain: 1.0}, source="dm"
        )
        await self.memory.pin(GUILD_A, self.c, rel, True, source="dm")
        npc_before, truth_before = await self.newest(rel, self.captain), await self.truth(rel)
        await self.hide(self.arlo)
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [rel])
        self.assertIn(self.captain, got.get(rel, []))
        self.assertNotIn(self.pc, got.get(rel, []))
        self.assertEqual(await self.newest(rel, self.captain), npc_before)
        self.assertEqual(await self.truth(rel), truth_before)

    async def test_an_audience_limited_clue_closes_and_undo_restores(self) -> None:
        clue = await self.add("Torn Map", type="clue", status=CONFIRMED)
        rel = await self.listed(clue)
        await self.memory.add_knower(GUILD_A, self.c, clue, self.ids["mara"], source="dm")
        done = await self.memory.set_audience_limited(GUILD_A, self.c, clue, True, source="dm")
        self.assertEqual(await self.knowers(rel), [])
        self.assertFalse(await self.believes(rel))
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_a_merge_that_makes_the_kept_entry_hidden_closes_its_own(self) -> None:
        keep = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        gone = await self.add(
            "Arlo Two", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )
        rel = await self.listed(keep)
        done = await self.memory.merge(GUILD_A, self.c, keep, gone, source="dm", dm_said_same=True)
        self.assertEqual(await self.knowers(rel), [])
        self.assertFalse(await self.believes(rel))
        assert done.batch is not None
        await self.memory.undo(GUILD_A, self.c, done.batch, source="dm")
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_a_big_hide_and_show_use_a_handful_of_statements(self) -> None:
        arlo = await self.add("Arlo", type="character", role="npc", status=CONFIRMED)
        for i in range(30):
            target = await self.add(f"Target {i}", type="character", role="npc", status=CONFIRMED)
            made = await self.memory.add_relation(
                GUILD_A, self.c, arlo, "knows", target, source="dm", confidence=1.0,
                status=CONFIRMED, secret=True,
            )  # fmt: skip
            await self.memory.set_connection_knowers(
                GUILD_A, self.c, str(made.value[0].id), [self.pc, self.ids["mara"]], source="dm"
            )
        count = {"n": 0}
        original = psycopg.AsyncConnection.execute

        async def counting(conn: Any, *args: Any, **kwargs: Any) -> Any:
            count["n"] += 1
            return await original(conn, *args, **kwargs)

        psycopg.AsyncConnection.execute = counting  # type: ignore[method-assign]
        try:
            await self.hide(arlo)
            hid = count["n"]
            count["n"] = 0
            await self.hide(arlo, False)
            shown = count["n"]
        finally:
            psycopg.AsyncConnection.execute = original  # type: ignore[method-assign]
        self.assertLess(hid, 60, f"{hid} statements to hide")
        self.assertLess(shown, 60, f"{shown} statements to show")


class JoinedThroughTheGuardsName(MyCharacters):
    """Ken hidden or shown does not take away what a character knew through the guard's name."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        await self.memory.set_connection_knowers(
            self.g, self.one.id, self.guard_link, [self.okra], source=DM_SOURCE
        )

    async def hide(self, entity: str, hidden: bool) -> Any:
        return await self.memory.set_hidden_from_players(
            self.g, self.one.id, entity, hidden, source="dm"
        )

    async def holds_guard_name(self) -> bool:
        found = await self.memory.aliases(
            self.g, self.one.id, entity_id=self.ken, include_secret=True
        )
        alias = next(x for x in found if x.text == self.label)
        got = await self.memory.statement_knowers(self.g, self.one.id, "alias", [alias.id])
        return self.okra in got.get(alias.id, [])

    async def kept(self, tag: str) -> None:
        rows = await self.memory.statement_knowers(
            self.g, self.one.id, CONNECTION, [self.guard_link]
        )
        self.assertIn(self.okra, rows.get(self.guard_link, []), tag + " rows")
        know = await self.memory.what_my_characters_know(self.g, self.one.id, BOB.id)
        self.assertTrue(any(c.relation_id == self.guard_link for c in know.connections), tag)

    async def test_hiding_ken_after_a_public_join_keeps_the_link_and_the_name(self) -> None:
        await self.hide(self.ken, False)
        await self.join()
        await self.kept("joined")
        held = await self.holds_guard_name()
        await self.hide(self.ken, True)
        await self.kept("Ken hidden")
        self.assertEqual(await self.holds_guard_name(), held)

    async def test_a_hidden_ken_revealed_and_hidden_again_keeps_it(self) -> None:
        await self.join()
        await self.hide(self.ken, False)
        await self.kept("revealed")
        await self.hide(self.ken, True)
        await self.kept("hidden again")

    async def test_hide_and_show_ken_after_a_public_join_keeps_it(self) -> None:
        await self.hide(self.ken, False)
        await self.join()
        await self.hide(self.ken, True)
        await self.hide(self.ken, False)
        await self.kept("hidden and shown")

    async def test_hiding_the_other_end_still_closes_the_link(self) -> None:
        await self.join()
        await self.hide(self.mira, True)
        rows = await self.memory.statement_knowers(
            self.g, self.one.id, CONNECTION, [self.guard_link]
        )
        self.assertNotIn(self.okra, rows.get(self.guard_link, []))
