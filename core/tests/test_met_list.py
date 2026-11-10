"""👣 Met but not named (#4045): the DM's list of beings met in play, read after the consent
re-check, and naming, joining and removing, each one write (the entry, its rank 0 and the row's
removal together, or none of it). Real Postgres, invented names."""

from __future__ import annotations

from typing import Any

from dmbot.memory import importance_rank
from dmbot.memory.met_store import MetRow, MetStore
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.store import MET_GONE, MET_NAME_TAKEN
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import met_list
from tests.test_memory_names import DM, GUILD, NamesTest

LINE_MS = 1_700_000_001_000
PLAYER = 8


class Pure(NamesTest):
    def test_the_words(self) -> None:
        row = MetRow("m" * 32, "s", 1_700_000_000, "barbarians", 2, (LINE_MS,), (DM,))
        self.assertEqual(met_list.label(row), "barbarians (2)")
        self.assertEqual(met_list.label(MetRow("m", "s", None, "a wolf", 1, (), ())), "a wolf")
        self.assertIn("(several)", met_list.label(MetRow("m", "s", None, "rats", 0, (), ())))
        self.assertEqual(met_list.count_text(3), "👣 Met but not named (3)")
        self.assertIn("99+", met_list.count_text(120))
        self.assertIn("2 creatures or people", met_list.notice(2))
        self.assertIn("1 creature or person", met_list.notice(1))


class TheList(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.met = MetStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD, self.sid, [Line(LINE_MS, DM, "Two barbarians jump out.", "x")]
        )

    async def keep(self, what: str = "barbarians", count: int = 2, speakers: tuple[int, ...] = (DM,)
                   ) -> str:  # fmt: skip
        await self.met.add(
            GUILD, self.campaign.id, self.sid, [(what, count, (LINE_MS,), speakers)],
            session_started_at=1_700_000_000,
        )  # fmt: skip
        rows = await self.met.of_session(GUILD, self.campaign.id, self.sid)
        return next(r.id for r in rows if r.what == what)

    async def listed(self, agreed: set[int] | None = None) -> list[MetRow]:
        return await self.met.listed(
            GUILD, self.campaign.id, opted_in={DM, PLAYER} if agreed is None else agreed
        )

    async def ranks(self, entity_id: str) -> list[tuple[int, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT rank, reason FROM memory_importance WHERE guild_id = %s AND entity_id = %s"
                " ORDER BY seq",
                (GUILD, entity_id),
            )
            return [(int(r["rank"]), str(r["reason"])) for r in await cur.fetchall()]

    async def test_the_list_leaves_out_and_deletes_rows_citing_someone_who_stopped(self) -> None:
        await self.keep("barbarians", speakers=(DM,))
        await self.keep("sled dogs", 7, speakers=(DM, PLAYER))
        self.assertEqual(len(await self.listed()), 2)
        self.assertEqual([r.what for r in await self.listed({DM})], ["barbarians"])
        self.assertEqual(await self.met.count(GUILD, self.campaign.id, opted_in={DM, PLAYER}), 1)

    async def test_naming_makes_the_entry_ranks_it_0_and_takes_it_off_the_list(self) -> None:
        met_id = await self.keep("barbarians", 2)
        done = await self.memory.name_met(GUILD, self.campaign.id, met_id, "The Red Axes")
        self.assertEqual((done.value.type, done.value.status), ("faction", CONFIRMED))
        self.assertEqual(done.value.description, "Met in play: barbarians (2)")
        self.assertFalse(done.value.unseen_by_players)  # (the party met them at the table)
        self.assertEqual(await self.ranks(done.value.id), [(0, importance_rank.MET_IN_PLAY)])
        self.assertEqual(await self.listed(), [])

    async def test_one_being_becomes_a_character(self) -> None:
        met_id = await self.keep("a wolf", 1)
        done = await self.memory.name_met(GUILD, self.campaign.id, met_id, "Greyfang")
        self.assertEqual((done.value.type, done.value.role), ("character", "npc"))

    async def test_a_name_the_campaign_has_is_refused_and_nothing_changes(self) -> None:
        await self.memory.add_entity(
            GUILD, self.campaign.id, name="Greyfang", type="character", status=CONFIRMED,
            source="dm",
        )  # fmt: skip
        met_id = await self.keep("a wolf", 1)
        with self.assertRaises(MemoryRuleError) as refused:
            await self.memory.name_met(GUILD, self.campaign.id, met_id, "greyfang")
        self.assertEqual(str(refused.exception), MET_NAME_TAKEN)
        self.assertEqual(len(await self.listed()), 1)  # (still waiting)

    async def test_a_second_press_finds_it_gone(self) -> None:
        met_id = await self.keep()
        await self.memory.name_met(GUILD, self.campaign.id, met_id, "The Red Axes")
        with self.assertRaises(MemoryRuleError) as gone:
            await self.memory.name_met(GUILD, self.campaign.id, met_id, "Other Axes")
        self.assertEqual(str(gone.exception), MET_GONE)

    async def test_joining_ranks_an_unranked_entry_0_and_never_demotes_a_ranked_one(self) -> None:
        cid = self.campaign.id
        wolf = await self.memory.add_entity(
            GUILD, cid, name="Greyfang", type="character", status=CONFIRMED, source="dm"
        )
        await self.memory.join_met(GUILD, cid, await self.keep("a wolf", 1), wolf.value.id)
        self.assertEqual(await self.ranks(wolf.value.id), [(0, importance_rank.MET_IN_PLAY)])
        boss = await self.memory.add_entity(
            GUILD, cid, name="Vellynne", type="character", status=CONFIRMED, source="dm"
        )
        await importance_rank.ImportanceStore(self.db).add(
            GUILD, cid, [importance_rank.Assessment(boss.value.id, 4, importance_rank.READING)]
        )
        await self.memory.join_met(GUILD, cid, await self.keep("a hooded mage", 1), boss.value.id)
        self.assertEqual(await self.ranks(boss.value.id), [(4, importance_rank.READING)])
        self.assertEqual(await self.listed(), [])

    async def test_remove_takes_only_the_row(self) -> None:
        met_id = await self.keep()
        self.assertTrue(await self.met.remove(GUILD, self.campaign.id, met_id))
        self.assertFalse(await self.met.remove(GUILD, self.campaign.id, met_id))
        self.assertEqual(await self.listed(), [])

    async def act(self, action: str, met_id: str, text: str = "", target: str = "") -> Any:
        from dmbot.memory import web_actions as wa

        return await wa.apply(
            self.memory, GUILD, {DM},
            wa.Action("a" * 32, self.campaign.id, met_id, DM, action, text=text, target_id=target),
        )  # fmt: skip

    async def test_the_websites_actions_do_the_same(self) -> None:
        from dmbot.memory import web_actions as wa

        named = await self.act("met-name", await self.keep("barbarians", 2), text="The Red Axes")
        self.assertEqual(named.error, "")
        taken = await self.act("met-name", await self.keep("a wolf", 1), text="the red axes")
        self.assertEqual(taken.error, wa.EXISTS)  # (the page offers Same as…)
        wolf = (await self.listed())[0].id
        joined = await self.act("met-join", wolf, target=named.result_id)
        self.assertEqual((joined.error, joined.result_id), ("", named.result_id))
        self.assertEqual((await self.act("met-join", wolf, target=named.result_id)).error, wa.GONE)
        dogs = await self.keep("sled dogs", 7)
        self.assertEqual((await self.act("met-remove", dogs)).error, "")
        self.assertEqual((await self.act("met-remove", dogs)).error, "")  # (a retry is done)
        self.assertEqual(await self.listed(), [])
