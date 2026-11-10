"""👣 Met but not named (#4045): the DM's list of beings met in play, read after the consent
re-check, and naming, joining and removing, each one write (the entry, its rank 0 and the row's
removal together, or none of it). Consent is read again at the press; one rule says what name is
taken, for the store and the list alike. Real Postgres, invented names."""

from __future__ import annotations

import unittest
from collections.abc import Collection
from typing import Any

from dmbot.memory import importance_rank
from dmbot.memory import web_actions as wa
from dmbot.memory.met_store import MetRow, MetStore
from dmbot.memory.models import CONFIRMED, MemoryRuleError
from dmbot.memory.store import MET_GONE, MET_NAME_TAKEN, MET_NAME_WAITING, MET_NO_ENTRY
from dmbot.transcript.models import Line
from dmbot.transcript.store import TranscriptStore
from dmbot.ui import met_list
from tests.test_memory_names import DM, GUILD, NamesTest

LINE_MS = 1_700_000_001_000
PLAYER = 8
ALL = {DM, PLAYER}


class Words(unittest.TestCase):
    def test_the_words(self) -> None:
        row = MetRow("m" * 32, "s", 1_700_000_000, "barbarians", 2, (LINE_MS,), (DM,))
        self.assertEqual(met_list.label(row), "barbarians (2)")
        self.assertEqual(met_list.label(MetRow("m", "s", None, "a wolf", 1, (), ())), "a wolf")
        self.assertIn("(several)", met_list.label(MetRow("m", "s", None, "rats", 0, (), ())))
        self.assertEqual(met_list.when(row), "met on 14 Nov 2023")
        self.assertEqual(met_list.count_text(3), "👣 Met but not named (3)")
        self.assertIn("99+", met_list.count_text(120))
        self.assertIn("2 creatures or people", met_list.notice(2))
        self.assertIn("1 creature or person", met_list.notice(1))
        text = met_list.choice_text(row)
        self.assertIn("Same as…", text)
        self.assertIn(met_list.SHOWN_ONCE_NAMED, text)  # (naming shows them: said before)

    def test_the_menu_keeps_to_discords_limits(self) -> None:
        async def build() -> met_list.MetList:
            rows = [
                MetRow(f"{n:032x}", "s", 1_700_000_000, "w" * 150, 2, (), ()) for n in range(30)
            ]
            return met_list.MetList("c" * 32, rows)

        import asyncio

        view = asyncio.run(build())
        options = view.pick.options
        self.assertEqual(len(options), met_list.SHOWN_MAX)
        self.assertTrue(
            all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in options)
        )


class TheList(NamesTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.met = MetStore(self.db)
        self.transcripts = TranscriptStore(self.db)
        self.sid = await self.transcripts.open_session(GUILD, self.campaign.id, 1_700_000_000)
        await self.transcripts.add_lines(
            GUILD, self.sid, [Line(LINE_MS, DM, "Two barbarians jump out.", "x")]
        )

    async def keep(
        self, what: str = "barbarians", count: int = 2, speakers: tuple[int, ...] = (DM,)
    ) -> str:
        await self.met.add(
            GUILD, self.campaign.id, self.sid, [(what, count, (LINE_MS,), speakers)],
            session_started_at=1_700_000_000,
        )  # fmt: skip
        rows = await self.met.of_session(GUILD, self.campaign.id, self.sid)
        return next(r.id for r in rows if r.what == what)

    async def listed(self, agreed: Collection[int] = frozenset(ALL)) -> list[MetRow]:
        return await self.met.listed(GUILD, self.campaign.id, opted_in=agreed)

    async def name(self, met_id: str, name: str, agreed: Collection[int] = frozenset(ALL)) -> Any:
        return await self.memory.name_met(GUILD, self.campaign.id, met_id, name, opted_in=agreed)

    async def join(self, met_id: str, entity_id: str) -> Any:
        return await self.memory.join_met(GUILD, self.campaign.id, met_id, entity_id, opted_in=ALL)

    async def add(self, name: str, type: str = "character", status: str = CONFIRMED) -> str:
        made = await self.memory.add_entity(
            GUILD, self.campaign.id, name=name, type=type, status=status, source="dm"
        )
        return made.value.id

    async def ranks(self, entity_id: str) -> list[tuple[int, str]]:
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT rank, reason FROM memory_importance WHERE guild_id = %s AND entity_id = %s"
                " ORDER BY seq",
                (GUILD, entity_id),
            )
            return [(int(r["rank"]), str(r["reason"])) for r in await cur.fetchall()]

    async def refused(self, met_id: str, name: str) -> str:
        with self.assertRaises(MemoryRuleError) as refused:
            await self.name(met_id, name)
        return str(refused.exception)

    async def test_the_list_leaves_out_and_deletes_rows_citing_someone_who_stopped(self) -> None:
        await self.keep("barbarians", speakers=(DM,))
        await self.keep("sled dogs", 7, speakers=(DM, PLAYER))
        self.assertEqual(await self.met.count(GUILD, self.campaign.id, opted_in={DM}), 1)
        self.assertEqual(len(await self.listed()), 2)  # (counting deleted nothing)
        self.assertEqual([r.what for r in await self.listed({DM})], ["barbarians"])
        self.assertEqual(len(await self.listed()), 1)  # (the read deleted it)

    async def test_naming_makes_the_entry_ranks_it_0_and_takes_it_off_the_list(self) -> None:
        done = await self.name(await self.keep("barbarians", 2), "The Red Axes")
        self.assertEqual((done.value.type, done.value.status), ("faction", CONFIRMED))
        self.assertEqual(done.value.description, "Met in play: barbarians (2)")
        self.assertFalse(done.value.unseen_by_players)  # (the party met them at the table)
        self.assertEqual(await self.ranks(done.value.id), [(0, importance_rank.MET_IN_PLAY)])
        self.assertEqual(await self.listed(), [])

    async def test_one_being_becomes_a_character(self) -> None:
        done = await self.name(await self.keep("a wolf", 1), "Greyfang")
        self.assertEqual((done.value.type, done.value.role), ("character", "npc"))

    async def test_someone_who_stopped_since_the_list_was_read_stops_the_naming(self) -> None:
        met_id = await self.keep("sled dogs", 7, speakers=(DM, PLAYER))
        with self.assertRaises(MemoryRuleError) as gone:
            await self.name(met_id, "Boy's team", agreed={DM})
        self.assertEqual(str(gone.exception), MET_GONE)
        self.assertEqual(await self.met.of_session(GUILD, self.campaign.id, self.sid), [])
        async with self.db.guild(GUILD) as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM memory_importance WHERE guild_id = %s", (GUILD,)
            )
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 0)  # (no entry, no rank: the words never became a note)

    async def test_what_is_taken_says_why(self) -> None:
        await self.add("Greyfang")
        await self.add("Snowpaw", status="proposed")
        self.assertEqual(
            await self.refused(await self.keep("a wolf", 1), "greyfang"), MET_NAME_TAKEN
        )
        self.assertEqual(
            await self.refused(await self.keep("a fox", 1), "Snowpaw"), MET_NAME_WAITING
        )
        self.assertEqual(len(await self.listed()), 2)  # (still waiting)
        owners = await self.memory.met_name_owners(GUILD, self.campaign.id, "Snowpaw")
        self.assertEqual([(o.confirmed, o.joinable) for o in owners], [(False, False)])

    async def test_a_turned_down_name_is_free_and_a_place_is_not_joinable(self) -> None:
        old = await self.add("Ashfur", status="proposed")
        await self.memory.set_entity_status(GUILD, self.campaign.id, old, "rejected", source="dm")
        done = await self.name(await self.keep("a cat", 1), "Ashfur")
        self.assertEqual(done.value.name, "Ashfur")
        hollow = await self.add("Grimsby Hollow", type="place")
        with self.assertRaises(MemoryRuleError) as refused:
            await self.join(await self.keep("barbarians", 2), hollow)
        self.assertEqual(str(refused.exception), MET_NO_ENTRY)
        (owner,) = await self.memory.met_name_owners(GUILD, self.campaign.id, "Grimsby Hollow")
        self.assertEqual((owner.confirmed, owner.joinable), (True, False))

    async def test_a_second_press_finds_it_gone(self) -> None:
        met_id = await self.keep()
        await self.name(met_id, "The Red Axes")
        self.assertEqual(await self.refused(met_id, "Other Axes"), MET_GONE)

    async def test_joining_ranks_an_unranked_entry_0_and_never_demotes_a_ranked_one(self) -> None:
        wolf = await self.add("Greyfang")
        await self.join(await self.keep("a wolf", 1), wolf)
        self.assertEqual(await self.ranks(wolf), [(0, importance_rank.MET_IN_PLAY)])
        boss = await self.add("Vellynne")
        await importance_rank.ImportanceStore(self.db).add(
            GUILD, self.campaign.id, [importance_rank.Assessment(boss, 4, importance_rank.READING)]
        )
        await self.join(await self.keep("a hooded mage", 1), boss)
        self.assertEqual(await self.ranks(boss), [(4, importance_rank.READING)])
        self.assertEqual(await self.listed(), [])

    async def test_a_rank_on_a_name_merged_in_is_kept_too(self) -> None:
        """The reviewer's case: the rank sits on a twin merged into the entry joined."""
        twin = await self.add("Old Vellynne")
        await importance_rank.ImportanceStore(self.db).add(
            GUILD, self.campaign.id, [importance_rank.Assessment(twin, 4, importance_rank.READING)]
        )
        boss = await self.add("Vellynne")
        await self.memory.merge(GUILD, self.campaign.id, boss, twin, source="dm", dm_said_same=True)
        await self.join(await self.keep("a hooded mage", 1), boss)
        ranks = await importance_rank.ImportanceStore(self.db).ranks(GUILD, self.campaign.id)
        self.assertEqual(ranks[boss], 4)

    async def test_remove_takes_only_the_row(self) -> None:
        met_id = await self.keep()
        self.assertTrue(await self.met.remove(GUILD, self.campaign.id, met_id))
        self.assertFalse(await self.met.remove(GUILD, self.campaign.id, met_id))
        self.assertEqual(await self.listed(), [])

    async def test_another_campaigns_row_is_not_found(self) -> None:
        other = await self.campaigns.create(GUILD, "Another table", DM)
        met_id = await self.keep()
        self.assertFalse(await self.met.remove(GUILD, other.id, met_id))
        with self.assertRaises(MemoryRuleError) as gone:
            await self.memory.name_met(GUILD, other.id, met_id, "The Red Axes", opted_in=ALL)
        self.assertEqual(str(gone.exception), MET_GONE)

    async def act(
        self, action: str, met_id: str, text: str = "", target: str = "", user: int = DM,
        agreed: Collection[int] = frozenset(ALL),
    ) -> Any:  # fmt: skip
        async def recorded() -> Collection[int]:
            return agreed

        waiting = wa.Waiting(claims=None, backstory=None, opted_in=recorded)  # type: ignore[arg-type]
        return await wa.apply(
            self.memory, GUILD, {DM},
            wa.Action(
                "a" * 32, self.campaign.id, met_id, user, action, text=text, target_id=target
            ),
            waiting,
        )  # fmt: skip

    async def test_the_websites_actions_do_the_same(self) -> None:
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

    async def test_the_website_checks_the_dm_the_words_and_who_is_recorded(self) -> None:
        met_id = await self.keep("rats", 0, speakers=(DM, PLAYER))
        self.assertEqual((await self.act("met-name", met_id, text="x", user=PLAYER)).error,
                         wa.NOT_ALLOWED)  # fmt: skip
        self.assertEqual((await self.act("met-name", met_id, text="  ")).error, wa.BAD_NAME)
        self.assertEqual(
            (await self.act("met-name", met_id, text="Rat King", agreed={DM})).error, wa.GONE
        )
