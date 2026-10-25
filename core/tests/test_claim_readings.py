"""A claim that is a reading (#2412, slice 2a): stored beside its words, asked in no list yet, never
confirmed as a fact, carried in a backup and checked when it comes from a file. Needs the test
database; invented names throughout."""

from __future__ import annotations

import json
from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory import readings
from dmbot.memory.backup import MemorySection
from dmbot.memory.claims import ClaimStore, Extracted
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_claims import said
from tests.test_memory_store import DM, GUILD_A, ConfirmedMemoryTest

THEORY = readings.BeliefProposal(
    "pc:Mira", "Bram", "carries", "Heartseeker", 0.75, "accusation", ("1",)
)


class Stored(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.claims = ClaimStore(self.memory)
        self.mira = await self.add("Bram")
        self.sword = await self.add("Heartseeker", type="item")

    async def propose(self, *items: Extracted) -> Any:
        return await self.claims.propose(
            GUILD_A, self.c, list(items), {"1": DM, "2": DM}, opted_in={DM}, dms={DM},
            prompt_version="r1", model="m1", session_started_at=1_000,
        )  # fmt: skip

    async def stored(self) -> list[dict[str, Any]]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM memory_claims ORDER BY created_at, id")
            return [dict(r) for r in await cur.fetchall()]

    async def test_a_reading_is_stored_beside_its_words_and_a_plain_claim_has_none(self) -> None:
        got = await self.propose(
            readings.as_claim(THEORY, "player_said"), said("Bram", "knows", "Heartseeker")
        )
        self.assertEqual((got.waiting, got.dropped), (2, 0))
        by_rel = {r["relationship"]: r for r in await self.stored()}
        reading = by_rel["carries"]["reading"]
        self.assertEqual(
            reading,
            {"view": "pc:Mira", "confidence": 0.75, "status": "accusation", "because": "",
             "manipulated_by": "", "new_assertion": False, "dm_confirms": False},
        )  # fmt: skip
        self.assertIsNone(by_rel["knows"]["reading"])
        self.assertNotRegex(json.dumps(reading), r"\d{6,}")  # no Discord id, no line

    async def test_the_same_words_as_a_theory_and_as_a_plain_claim_are_two_questions(self) -> None:
        await self.propose(readings.as_claim(THEORY, "player_said"))
        again = await self.propose(
            said("Bram", "carries", "Heartseeker", "player_said"),
            readings.as_claim(THEORY, "player_said"),
        )
        self.assertEqual(again.repeated, 1)  # only the theory is a repeat
        self.assertEqual(len(await self.stored()), 2)

    async def test_a_reading_is_not_asked_as_a_fact_and_cannot_be_said_yes_to_as_one(self) -> None:
        await self.propose(readings.as_claim(THEORY, "player_said"))
        self.assertEqual(await self.claims.waiting(GUILD_A, self.c, opted_in={DM}), [])
        (row,) = await self.stored()
        with self.assertRaises(MemoryRuleError) as raised:
            await self.claims.confirm(GUILD_A, self.c, row["id"], opted_in={DM})
        self.assertEqual(str(raised.exception), "That is a belief to place, not a fact to keep.")
        self.assertEqual(await self.memory.relations(GUILD_A, self.c), [])  # nothing was written

    async def test_a_backup_carries_the_reading_and_an_old_file_without_one_loads(self) -> None:
        await self.propose(readings.as_claim(THEORY, "player_said"))
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        (claim,) = [r for r in rows if r["table"] == "claim"]
        self.assertEqual(claim["reading"]["status"], "accusation")
        copy = (await self.campaigns.create(GUILD_A, "Copy", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, copy, rows)
            cur = await conn.execute(
                "SELECT reading FROM memory_claims WHERE campaign_id = %s", (copy,)
            )
            self.assertEqual([r["reading"]["view"] for r in await cur.fetchall()], ["pc:Mira"])
        old = [
            {k: v for k, v in r.items() if k != "reading"} if r["table"] == "claim" else r
            for r in rows
        ]
        older = (await self.campaigns.create(GUILD_A, "Older", DM)).id
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, older, old)
            cur = await conn.execute(
                "SELECT reading FROM memory_claims WHERE campaign_id = %s", (older,)
            )
            self.assertEqual([r["reading"] for r in await cur.fetchall()], [None])

    async def test_a_reading_from_a_file_is_checked(self) -> None:
        await self.propose(readings.as_claim(THEORY, "player_said"))
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        good = next(r["reading"] for r in rows if r["table"] == "claim")
        bad_ones: list[object] = [
            "text", [], {**good, "confidence": 2}, {**good, "confidence": True},
            {**good, "status": "certain"}, {**good, "view": "everyone"}, {**good, "view": "pc:"},
            {**good, "because": 5}, {**good, "because": "x" * 500}, {**good, "extra": 1},
            {k: v for k, v in good.items() if k != "view"}, {**good, "dm_confirms": "yes"},
        ]  # fmt: skip
        for reading in bad_ones:
            with self.subTest(str(reading)[:60]):
                tampered = [{**r, "reading": reading} if r["table"] == "claim" else r for r in rows]
                with self.assertRaises(CampaignError):
                    section.check(tampered)


class NoNewNames(Stored):
    async def test_a_reading_with_an_unknown_name_is_dropped_and_never_becomes_a_name(self) -> None:
        stranger = readings.BeliefProposal(
            "pc:Bram", "Zed", "carries", "Heartseeker", 0.5, "theory", ("1",)
        )
        got = await self.claims.propose(
            GUILD_A, self.c, [readings.as_claim(stranger, "player_said")], {"1": DM},
            opted_in={DM}, dms={DM}, prompt_version="r1", model="m1",
            session_started_at=1_000, make_names=False,
        )  # fmt: skip
        self.assertEqual((got.waiting, got.dropped, got.named, got.deferred), (0, 1, 0, 0))
        (row,) = await self.stored()
        self.assertEqual((row["status"], row["reading"] is not None), ("dropped", True))
        names = {e.name for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertNotIn("Zed", names)
        # and the next claims pass has no leftover to turn into one
        await self.propose(said("Bram", "knows", "Heartseeker"))
        names = {e.name for e in await self.memory.entities(GUILD_A, self.c)}
        self.assertNotIn("Zed", names)
        waiting = [
            r for r in await self.stored() if r["reason"] == "waiting for its name to be proposed"
        ]
        self.assertEqual(waiting, [])

    async def test_a_known_name_reading_is_stored_with_its_reading_and_makes_no_entity(
        self,
    ) -> None:
        before = len(await self.memory.entities(GUILD_A, self.c))
        got = await self.claims.propose(
            GUILD_A, self.c, [readings.as_claim(THEORY, "player_said")], {"1": DM},
            opted_in={DM}, dms={DM}, prompt_version="readings-1", model="m1",
            session_started_at=1_000, make_names=False,
        )  # fmt: skip
        self.assertEqual((got.waiting, got.named), (1, 0))
        (row,) = await self.stored()
        self.assertEqual(row["reading"]["status"], "accusation")
        self.assertEqual(len(await self.memory.entities(GUILD_A, self.c)), before)


class EveryPathLeavesItAlone(Stored):
    async def reading_row(self) -> dict[str, Any]:
        await self.propose(readings.as_claim(THEORY, "player_said"))
        (row,) = await self.stored()
        return row

    async def test_the_pre_session_count_and_the_health_count_leave_readings_out(self) -> None:
        await self.reading_row()
        self.assertEqual(await self.claims.waiting_for_check(GUILD_A, self.c, opted_in={DM}), 0)
        from dmbot.memory import health

        got = await health.read(self.memory, GUILD_A, self.c, since=0)
        self.assertEqual(got.asked_dm, 0)

    async def test_when_a_speaker_stops_the_reading_is_wiped_with_the_words(self) -> None:
        row = await self.reading_row()
        await self.claims.purge_stopped(GUILD_A, self.c, opted_in=set())  # nobody is recorded
        (after,) = await self.stored()
        self.assertEqual(after["id"], row["id"])
        self.assertEqual((after["status"], after["subject"], after["lines"]), ("dropped", "", []))
        self.assertIsNone(after["reading"])  # not the clue, nor the character it named

    async def test_stop_recording_me_deletes_a_waiting_reading(self) -> None:
        await self.reading_row()
        self.assertEqual(await self.claims.forget_speaker(GUILD_A, DM), 1)
        self.assertEqual(await self.stored(), [])

    async def test_a_file_cannot_bring_a_reading_back_as_a_kept_fact(self) -> None:
        await self.reading_row()
        section = MemorySection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
        tampered = [
            {**r, "status": "confirmed", "fact_id": "f" * 32} if r["table"] == "claim" else r
            for r in rows
        ]
        with self.assertRaises(CampaignError):
            section.check(tampered)
