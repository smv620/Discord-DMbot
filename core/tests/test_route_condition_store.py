"""A route's condition in the store (#4258, MapBot layers slice 4a; Supervisor's ruling on
#4258): on a route place or a known road between two places, never a trip; one value at a time,
a change kept as history from the story's time; DMbot's reads for the DM to check, words of change
only ending a row; the players' rule; Undo; a road's condition going with its road; a backup
carrying both. Invented names. Needs Postgres."""

from __future__ import annotations

import psycopg

from dmbot.campaigns.store import decode_backup, encode_backup
from dmbot.memory import gate, link_edits
from dmbot.memory import map_facts as mf
from dmbot.memory._changes import RELATIONS
from dmbot.memory.map_fact_store import ONLY_KNOWN_ROAD, Condition, Read
from dmbot.memory.models import MemoryRuleError, new_id
from tests.test_memory_store import GUILD_A, GUILD_B, MemoryTest

DM = "dm"
SESSION_5 = 5_000


class RouteCondition(MemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.pass_ = await self.add("Icewind Pass", type="place", status="confirmed")
        await self.memory.set_place_form(GUILD_A, self.c, self.pass_, "route", source=DM)
        self.town = await self.add("Bryn Shander", type="place", status="confirmed")
        self.targos = await self.add("Targos", type="place", status="confirmed")
        made = await self.relate(
            self.town, "route_to", self.targos, detail="road", status="confirmed"
        )
        self.road = made.value[0].id

    async def session(self, started_at: int) -> None:
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "INSERT INTO transcript_sessions (id, guild_id, campaign_id, started_at)"
                " VALUES (%s, %s, %s, %s)",
                (new_id(), GUILD_A, self.c, started_at),
            )

    async def set(self, value: str | None, **kw: str) -> int | None:
        kw.setdefault("place_id", self.pass_)
        return (
            await self.memory.set_route_condition(GUILD_A, self.c, value, source=DM, **kw)
        ).batch

    async def cond(
        self, target: str | None = None, viewer: gate.Viewer | None = None
    ) -> Condition | None:
        target = target or self.pass_
        got = await self.memory.route_conditions(
            GUILD_A, self.c, viewer or gate.Viewer.the_dm(),
            place_ids=[target], relation_ids=[target],
        )  # fmt: skip
        return got.get(target)

    async def read(self, *found: Read) -> dict[str, int]:
        got = await self.memory.add_read_map_facts(
            GUILD_A, self.c, list(found), source="background"
        )
        return dict(got.value)

    async def test_a_change_keeps_the_old_one_as_history_and_undo_takes_it_back(self) -> None:
        await self.set("open")
        first = await self.cond()
        assert first is not None
        self.assertEqual((first.now.value, first.since_at, first.before), ("open", None, None))
        await self.session(SESSION_5)
        change = await self.set("closed")
        got = await self.cond()
        assert got is not None and got.before is not None
        self.assertEqual(
            (got.now.value, got.since_at, got.before.value), ("closed", SESSION_5, "open")
        )
        await self.memory.undo(GUILD_A, self.c, change)
        back = await self.cond()
        assert back is not None
        self.assertEqual((back.now.value, back.before), ("open", None))

    async def test_before_any_session_it_changes_from_now(self) -> None:
        await self.set("open")
        await self.set("hard")
        got = await self.cond()
        assert got is not None and got.before is not None
        self.assertEqual(got.since_at, int(self.now))  # (no session: the change's own time)
        self.assertEqual(got.before.value, "open")

    async def test_the_same_value_again_changes_nothing_and_not_sure_takes_it_off(self) -> None:
        await self.set("closed")
        await self.set("closed")
        got = await self.cond()
        assert got is not None
        self.assertIsNone(got.before)
        await self.set(None)
        self.assertIsNone(await self.cond())

    async def test_own_words(self) -> None:
        await self.set("other", words=" washed out at the ford ")
        got = await self.cond()
        assert got is not None
        self.assertEqual((got.now.value, got.now.words), ("other", "washed out at the ford"))
        with self.assertRaises(MemoryRuleError):
            await self.set("other")

    async def test_only_a_route_place_or_a_known_road(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "Only a road, a pass or a bridge"):
            await self.set("closed", place_id=self.town)
        trip = await self.relate(
            self.targos, "route_to", self.town, detail="road", status="confirmed",
            route_state="travelled", from_session_at=SESSION_5,
        )  # fmt: skip
        with self.assertRaises(MemoryRuleError) as known:
            await self.set("closed", place_id="", relation_id=trip.value[0].id)
        self.assertEqual(str(known.exception), ONLY_KNOWN_ROAD)
        # (and the table itself refuses a trip, whatever writes it)
        with self.assertRaises(psycopg.errors.CheckViolation):
            async with self.db.guild(GUILD_A) as conn:
                await conn.execute(
                    "INSERT INTO memory_map_facts (guild_id, campaign_id, id, relation_id, layer,"
                    " value, status, source, created_at)"
                    " VALUES (%s, %s, %s, %s, 'condition', 'closed', 'confirmed', 'dm', 1)",
                    (GUILD_A, self.c, new_id(), trip.value[0].id),
                )

    async def test_a_road_between_two_places(self) -> None:
        await self.set("gone", place_id="", relation_id=self.road)
        got = await self.cond(self.road)
        assert got is not None
        self.assertEqual(
            (got.now.value, got.now.relation_id, got.now.place_id), ("gone", self.road, "")
        )

    async def test_a_road_taken_away_takes_its_condition_and_undo_brings_both(self) -> None:
        await self.set("closed", place_id="", relation_id=self.road)
        road = await self.memory.relation(GUILD_A, self.c, self.road)
        assert road is not None
        removed = await link_edits.remove(self.memory, GUILD_A, self.c, road)  # (🗑 Remove)
        self.assertIsNone(await self.cond(self.road))
        await self.memory.undo(GUILD_A, self.c, removed.batch)
        got = await self.cond(self.road)
        assert got is not None
        self.assertEqual(got.now.value, "closed")
        # a road deleted outright (a merge folding it into its twin): its condition goes logged
        async with self.memory._write(GUILD_A, self.c, DM) as w:
            await w.delete(RELATIONS, self.road)
            batch = w.batch
        self.assertIsNone(await self.cond(self.road))
        await self.memory.undo(GUILD_A, self.c, batch)
        self.assertIsNotNone(await self.cond(self.road))

    async def test_dmbots_read_waits_and_a_dm_correction_rejects_it(self) -> None:
        counts = await self.read(Read(self.pass_, mf.CONDITION, "closed"))
        self.assertEqual(counts["written"], 1)
        got = await self.cond()
        assert got is not None
        self.assertEqual(got.now.status, mf.READ)
        await self.set("open")  # (a guess the DM corrects is no part of the story)
        got = await self.cond()
        assert got is not None
        self.assertEqual((got.now.value, got.before), ("open", None))

    async def test_words_of_change_end_a_read_but_never_the_dms_word(self) -> None:
        self.assertEqual(
            (await self.read(Read(self.pass_, mf.CONDITION, "open", changed=True)))["change_alone"],
            1,
        )  # ("reopened" with nothing to end says nothing)
        self.assertIsNone(await self.cond())
        await self.read(Read(self.pass_, mf.CONDITION, "closed"))
        closed = await self.read(Read(self.pass_, mf.CONDITION, "open", changed=True))
        self.assertEqual(closed["closed"], 1)
        got = await self.cond()
        assert got is not None and got.before is not None
        self.assertEqual((got.now.value, got.before.value), ("open", "closed"))
        await self.set("closed")
        noted = await self.read(Read(self.pass_, mf.CONDITION, "hard"))
        self.assertEqual(noted["noted"], 1)  # (the DM's own word stands: a World note)
        # (and a value the DM corrected away is never read in again, as for every layer)
        again = await self.read(Read(self.pass_, mf.CONDITION, "open", changed=True))
        self.assertEqual(again["rejected_before"], 1)
        # (another phrase with no words of change is only noted against another read, too)
        await self.set(None)
        await self.read(Read(self.pass_, mf.CONDITION, "hard"))
        self.assertEqual((await self.read(Read(self.pass_, mf.CONDITION, "gone")))["noted"], 1)

    async def test_players_see_only_what_the_dm_kept_and_showed(self) -> None:
        pc = await self.add("Testa", type="player_character", played_by=9, status="confirmed")
        party = gate.Viewer.player([pc])
        for kind, target in ((gate.ENTRY, self.pass_), (gate.CONNECTION, self.road)):
            await self.memory.set_statement_knowers(GUILD_A, self.c, kind, target, [pc], source=DM)
        await self.read(Read(self.pass_, mf.CONDITION, "closed"))
        self.assertIsNone(await self.cond(viewer=party))  # (DMbot's read is the DM's)
        await self.memory.keep_map_facts(GUILD_A, self.c, self.pass_, source=DM)
        self.assertIsNone(await self.cond(viewer=party))  # (kept, not shown yet)
        await self.memory.show_map_facts(GUILD_A, self.c, self.pass_, True, source=DM)
        got = await self.cond(viewer=party)
        assert got is not None
        self.assertEqual(got.now.value, "closed")
        # a road: shown and kept by its own buttons
        await self.read(Read("", mf.CONDITION, "hard", relation_id=self.road))
        await self.memory.keep_road_condition(GUILD_A, self.c, self.road, source=DM)
        await self.memory.show_road_condition(GUILD_A, self.c, self.road, True, source=DM)
        road = await self.cond(self.road, viewer=party)
        assert road is not None
        self.assertEqual((road.now.value, road.now.status), ("hard", mf.CONFIRMED))

    async def test_a_backup_carries_a_place_and_a_road_with_their_history(self) -> None:
        await self.set("open")
        await self.session(SESSION_5)
        await self.set("closed")
        await self.set("hard", place_id="", relation_id=self.road)
        raw = encode_backup(await self.campaigns.export(GUILD_A, self.c))
        restored = await self.campaigns.import_backup(GUILD_B, decode_backup(raw), DM_USER)
        async with self.db.guild(GUILD_B) as conn:
            cur = await conn.execute(
                "SELECT layer, value, entity_id IS NULL AS on_road, to_session_at IS NULL AS open"
                " FROM memory_map_facts WHERE guild_id = %s AND campaign_id = %s"
                " AND layer = 'condition' ORDER BY value",
                (GUILD_B, restored.id),
            )
            rows = [tuple(r.values()) for r in await cur.fetchall()]
        self.assertEqual(
            rows,
            [
                ("condition", "closed", False, True),
                ("condition", "hard", True, True),
                ("condition", "open", False, False),
            ],
        )


DM_USER = 1001
