"""Hiding an entry closes the player characters' rows AND beliefs on what hangs off it, and showing
it again puts back exactly what that hide closed and nothing else (#3082); a connection a join
moved, and the secret name it was moved through, are known through the name, so hiding the entry
they now hang on does not close them (#3037). Invented names."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

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

        with patch.object(psycopg.AsyncConnection, "execute", counting):
            await self.hide(arlo)
            hid = count["n"]
            count["n"] = 0
            await self.hide(arlo, False)
            shown = count["n"]
        self.assertLess(hid, 60, f"{hid} statements to hide")
        self.assertLess(shown, 60, f"{shown} statements to show")


DAY = 24 * 60 * 60


class WhatTheHideKeeps(Base):
    """The mark lives in the row, so pruning, the order of shows and many edits cannot lose it."""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claim = "c" * 32
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO memory_claims (guild_id, campaign_id, id, subject, relationship,"
                " object, how, lines, source, prompt_version, model, status, created_at)"
                " VALUES (%s, %s, %s, 'Arlo', 'knows', 'Brin', 'dm_said', %s, 'extractor', 'v1',"
                " 'm', 'confirmed', 1)",
                (GUILD_A, self.c, self.claim, []),
            )

    async def krows(self, rel: str) -> list[Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT perspective_id, confidence, hidden_by FROM memory_knowers"
                " WHERE guild_id = %s AND campaign_id = %s AND relation_id = %s"
                " ORDER BY perspective_id",
                (GUILD_A, self.c, rel),
            )
            return list(await cur.fetchall())

    async def two_ends(self, *, first_hidden: bool = False) -> tuple[str, str, str]:
        a = await self.add(
            "Arlo", type="character", role="npc", status=CONFIRMED, hidden_from_players=first_hidden
        )
        b = await self.add("Brin", type="character", role="npc", status=CONFIRMED)
        made = await self.memory.add_relation(
            GUILD_A, self.c, a, "knows", b, source="dm", confidence=1.0,
            status=CONFIRMED, secret=True,
        )  # fmt: skip
        rel = str(made.value[0].id)
        await self.memory.set_connection_knowers(GUILD_A, self.c, rel, [self.pc], source="dm")
        return a, b, rel

    async def test_a_hide_a_month_old_and_pruned_still_comes_back(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        self.now += 31 * DAY
        await self.memory.prune_changes(GUILD_A, self.c)
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_many_edits_while_hidden_do_not_lose_it(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        for i in range(25):
            await self.memory.rename_entity(
                GUILD_A, self.c, self.arlo, f"Arlo {i}", source="dm", keep_old=False
            )
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_a_dm_change_to_the_row_while_hidden_leaves_row_and_belief_as_set(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        await self.memory.set_statement_knowers(
            GUILD_A, self.c, CONNECTION, rel, {self.pc: 0.5}, source="dm"
        )
        mid = await self.krows(rel), await self.newest(rel, self.pc)
        await self.hide(self.arlo, False)
        self.assertEqual((await self.krows(rel), await self.newest(rel, self.pc)), mid)

    async def test_a_list_the_dm_names_while_hidden_is_the_list(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        await self.memory.set_connection_knowers(
            GUILD_A, self.c, rel, [self.ids["mara"]], source="dm"
        )
        await self.hide(self.arlo, False)
        known = await self.knowers(rel)
        self.assertEqual(known, [self.ids["mara"]])  # (and not Okthandom, who slept)
        self.assertFalse(await self.believes(rel))

    async def test_hiding_and_showing_twice_restores_once(self) -> None:
        rel = await self.listed()
        first = await self.newest(rel, self.pc)
        for _ in range(2):
            await self.hide(self.arlo)
            await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertEqual(await self.newest(rel, self.pc), first)
        self.assertIn(rel, await self.mine())

    async def test_two_hidden_ends_come_back_in_either_order(self) -> None:
        for order in ((0, 1), (1, 0)):
            with self.subTest(order=order):
                a, b, rel = await self.two_ends()
                ends = (a, b)
                await self.hide(a)
                await self.hide(b)
                await self.hide(ends[order[0]], False)
                self.assertEqual(await self.knowers(rel), [])  # one end is still hidden
                self.assertFalse(await self.believes(rel))
                await self.hide(ends[order[1]], False)
                self.assertEqual(await self.knowers(rel), [self.pc])
                self.assertTrue(await self.believes(rel))

    async def test_an_end_hidden_from_the_start_keeps_the_list_through_hiding_the_other(
        self,
    ) -> None:
        _, b, rel = await self.two_ends(first_hidden=True)
        before = await self.knowers(rel), await self.believes(rel)
        await self.hide(b)
        self.assertEqual(await self.knowers(rel), [])
        await self.hide(b, False)
        self.assertEqual((await self.knowers(rel), await self.believes(rel)), before)

    async def test_a_player_character_made_an_npc_meanwhile_gets_nothing_back(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        await self.memory.set_entity_type(
            GUILD_A, self.c, self.pc, "character", role="npc", source="dm"
        )
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [])

    async def test_a_merge_into_a_hidden_entry_then_showing_the_kept_one(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        other = await self.add(
            "Arlo Two", type="character", role="npc", status=CONFIRMED, hidden_from_players=True
        )
        await self.memory.merge(GUILD_A, self.c, self.arlo, other, source="dm", dm_said_same=True)
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_undoing_a_show_and_showing_again_restores_once(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        shown = await self.hide(self.arlo, False)
        assert shown.batch is not None
        await self.memory.undo(GUILD_A, self.c, shown.batch, source="dm")
        self.assertEqual(await self.knowers(rel), [])
        await self.hide(self.arlo, False)
        self.assertEqual(await self.knowers(rel), [self.pc])
        self.assertTrue(await self.believes(rel))

    async def test_a_sleeping_row_is_known_to_no_reader(self) -> None:
        rel = await self.listed()
        await self.hide(self.arlo)
        self.assertEqual(await self.krows_hidden_by(rel), [[self.arlo]])
        self.assertEqual(await self.knowers(rel), [])
        got = await self.memory.connection_knowers(GUILD_A, self.c, [rel])
        self.assertEqual(got.get(rel, []), [])
        self.assertNotIn(rel, await self.mine())

    async def krows_hidden_by(self, rel: str) -> list[Any]:
        return [list(r["hidden_by"]) for r in await self.krows(rel)]

    async def test_the_belief_comes_back_with_its_status_and_evidence(self) -> None:
        rel = await self.listed()
        persp = {p.entity_id: p.id for p in await self.memory.perspectives(GUILD_A, self.c)}
        await self.memory.believe(
            GUILD_A, self.c, persp[self.pc], rel, 1.0, "secret_plot", source="dm",
            evidence=[self.claim],
        )  # fmt: skip
        before = await self.newest(rel, self.pc)
        await self.hide(self.arlo)
        await self.hide(self.arlo, False)
        self.assertEqual(await self.newest(rel, self.pc), before)
        self.assertEqual(await self.evidence_of_newest(rel), [("claim", self.claim)])

    async def evidence_of_newest(self, rel: str) -> list[tuple[str, str | None]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute(
                "SELECT e.kind, e.target_id FROM memory_evidence e WHERE e.guild_id = %s"
                " AND e.campaign_id = %s AND e.belief_id = (SELECT b.id FROM memory_beliefs b"
                " JOIN memory_perspectives p ON p.guild_id = b.guild_id"
                " AND p.campaign_id = b.campaign_id AND p.id = b.perspective_id"
                " WHERE b.guild_id = %s AND b.campaign_id = %s AND b.relation_id = %s"
                " AND p.entity_id = %s ORDER BY b.seq DESC LIMIT 1)",
                (GUILD_A, self.c, GUILD_A, self.c, rel, self.pc),
            )
            return [(r["kind"], r["target_id"]) for r in await cur.fetchall()]

    async def test_a_backup_carries_the_sleep_and_a_stranger_in_the_mark_is_dropped(self) -> None:
        from dmbot.memory.backup import MemorySection

        rel = await self.listed()
        await self.hide(self.arlo)
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        for r in rows:
            if r.get("table") == "knower" and r["relation_id"] == rel:
                r["hidden_by"] = [*r["hidden_by"], "f" * 32]  # an entry the file does not hold
        copy = (await self.campaigns.create(GUILD_A, "Copy", 7)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, rows)
            cur = await conn.execute(
                "SELECT hidden_by FROM memory_knowers WHERE campaign_id = %s"
                " AND relation_id IS NOT NULL AND cardinality(hidden_by) > 0",
                (copy,),
            )
            kept = [list(r["hidden_by"]) for r in await cur.fetchall()]
        self.assertEqual(kept, [[self.arlo]])


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
        self.assertTrue(held)  # (Okra does hold the guard's name to begin with)
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

    async def test_the_websites_helpers_leave_a_sleeping_row_out(self) -> None:
        found = await self.sql_one(
            "SELECT count(*) FILTER (WHERE strpos(prosrc, 'memory_knowers k') > 0) AS bare,"
            " count(*) FILTER (WHERE strpos(prosrc, 'cardinality(hidden_by) = 0') > 0) AS guarded"
            " FROM pg_proc WHERE pronamespace = current_schema()::regnamespace"
            " AND left(proname, 6) = 'dmbot_'"
        )
        self.assertEqual(found["bare"], 0)  # (every read of the table is the awake rows only)
        self.assertGreater(found["guarded"], 0)

    async def test_hiding_the_other_end_closes_the_link_and_showing_it_gives_it_back(self) -> None:
        await self.join()
        await self.hide(self.mira, True)
        rows = await self.memory.statement_knowers(
            self.g, self.one.id, CONNECTION, [self.guard_link]
        )
        self.assertNotIn(self.okra, rows.get(self.guard_link, []))
        await self.hide(self.mira, False)
        await self.kept("Mira shown again")
