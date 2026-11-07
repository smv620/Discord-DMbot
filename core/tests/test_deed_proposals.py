"""Deeds the session reader proposed, waiting for the DM (#3175 slice 4). Real Postgres; invented
names. A proposal is never the story: only the DM's record is."""

from __future__ import annotations

from dmbot.campaigns.models import CampaignError
from dmbot.memory.deed_proposals import DeedProposals, Stored, Waiting, forget
from dmbot.memory.deed_proposals_backup import DeedProposalsSection
from dmbot.memory.deed_reading import DeedProposal
from dmbot.memory.models import MemoryRuleError
from tests.test_memory_store import GUILD_A, ConfirmedMemoryTest

DM_ID, ALICE, BOB = 7, 8, 9
RECORDED = {DM_ID, ALICE}


def deed(
    doer: str = "Cerric", viewers: tuple[str, ...] = ("Brindle",), text: str = "Saved the mill"
) -> DeedProposal:
    return DeedProposal(doer, text, viewers, "Helped them", ("1", "2"))


class Base(ConfirmedMemoryTest):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.deeds = DeedProposals(self.memory)
        self.cerric = await self.add(
            "Cerric", type="character", role="player_character", played_by=ALICE
        )
        self.brindle = await self.add("Brindle", type="place")
        await self.memory.link_party(GUILD_A, self.c, {ALICE})

    async def put(
        self,
        *proposals: tuple[DeedProposal, tuple[int, ...]],
        session: int | None = 500,
        recorded: set[int] = RECORDED,
    ) -> Stored:
        return await self.deeds.store(
            GUILD_A, self.c, list(proposals), opted_in=recorded, session_started_at=session,
            prompt_version="deeds-1", model="m",
        )  # fmt: skip

    async def waiting(self) -> list[Waiting]:
        return await self.deeds.waiting(GUILD_A, self.c, opted_in=RECORDED)


class Storing(Base):
    async def test_a_proposal_waits_with_numbers_and_never_a_lines_words(self) -> None:
        got = await self.put((deed(), (DM_ID,)))
        self.assertEqual((got.stored, got.repeated, got.not_recorded), (1, 0, 0))
        (w,) = await self.waiting()
        self.assertEqual((w.doer, w.viewers, w.effect), ("Cerric", ("Brindle",), "Helped them"))
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM memory_deed_proposals")
            (row,) = await cur.fetchall()
        self.assertEqual(
            (row["lines"], row["speaker_ids"], row["prompt_version"]),
            (["1", "2"], [DM_ID], "deeds-1"),
        )
        self.assertEqual(row["status"], "proposed")

    async def test_a_speaker_who_is_not_recorded_now_keeps_it_from_being_stored(self) -> None:
        got = await self.put((deed(), (DM_ID, BOB)))  # (Bob is not in RECORDED)
        self.assertEqual((got.stored, got.not_recorded), (0, 1))
        self.assertEqual(await self.waiting(), [])

    async def test_one_deed_for_a_pair_holds_across_re_runs_and_any_status(self) -> None:
        await self.put((deed(), (DM_ID,)))
        again = await self.put((deed(text="Again"), (DM_ID,)))
        self.assertEqual((again.stored, again.repeated), (0, 1))
        (w,) = await self.waiting()
        await self.deeds.dismiss(GUILD_A, self.c, w.id, source="dm")
        after = await self.put((deed(text="Once more"), (DM_ID,)))  # (dismissed still covers it)
        self.assertEqual(after.stored, 0)
        # another session is its own
        other = await self.put((deed(), (DM_ID,)), session=900)
        self.assertEqual(other.stored, 1)

    async def test_a_later_deed_keeps_only_the_viewers_not_yet_covered(self) -> None:
        await self.put((deed(), (DM_ID,)))
        got = await self.put((deed(viewers=("Brindle", "Oldmill")), (DM_ID,)))
        self.assertEqual((got.stored, got.repeated), (1, 1))
        self.assertIn(("Oldmill",), [w.viewers for w in await self.waiting()])


class Consent(Base):
    async def test_a_speaker_who_stops_wipes_the_waiting_proposal(self) -> None:
        await self.put((deed(), (DM_ID,)))
        await self.put((deed(doer="Mira", viewers=("Oldmill",)), (ALICE,)))
        left = await self.deeds.waiting(GUILD_A, self.c, opted_in={ALICE})  # (the DM stopped)
        self.assertEqual([w.doer for w in left], ["Mira"])
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM memory_deed_proposals")
            row = await cur.fetchone()
        assert row is not None
        self.assertEqual(row["n"], 1)  # (deleted, not hidden)

    async def test_stop_recording_me_deletes_the_waiting_ones_and_scrubs_a_recorded_one(
        self,
    ) -> None:
        await self.put(
            (deed(), (ALICE,)),
            (deed(doer="Mira", viewers=("Oldmill",)), (BOB, ALICE)),
            recorded={ALICE, BOB},
        )
        await self.memory.add_entity(
            GUILD_A, self.c, name="Oldmill", type="place", status="confirmed", source="dm"
        )
        recorded = await self.deeds.waiting(GUILD_A, self.c, opted_in={ALICE, BOB})
        first = next(w for w in recorded if w.doer == "Cerric")
        await self.deeds.record(GUILD_A, self.c, first.id, opted_in={ALICE, BOB}, source="dm")
        async with self.db.guild(GUILD_A) as conn:
            gone = await forget(conn, GUILD_A, ALICE)
            cur = await conn.execute("SELECT status, speaker_ids, lines FROM memory_deed_proposals")
            rows = await cur.fetchall()
        self.assertEqual(gone, 1)  # (the waiting one)
        (left,) = rows
        self.assertEqual((left["status"], left["speaker_ids"], left["lines"]), ("recorded", [], []))

    async def test_the_claims_stop_recording_me_path_clears_them_too(self) -> None:
        from dmbot.memory.claims import ClaimStore

        await self.put((deed(), (ALICE,)))
        await ClaimStore(self.memory).forget_speaker(GUILD_A, ALICE)
        self.assertEqual(await self.waiting(), [])

    async def test_the_count_leaves_out_what_a_stopped_speaker_made(self) -> None:
        await self.put((deed(), (DM_ID,)))
        self.assertEqual(await self.deeds.count_waiting(GUILD_A, self.c, opted_in=RECORDED), 1)
        self.assertEqual(await self.deeds.count_waiting(GUILD_A, self.c, opted_in={ALICE}), 0)


class Answering(Base):
    async def one(self) -> str:
        await self.put((deed(), (DM_ID,)))
        (w,) = await self.waiting()
        return w.id

    async def test_record_is_the_dms_add_deed_once_and_one_undo_takes_it_back(self) -> None:
        pid = await self.one()
        batch = await self.deeds.record(GUILD_A, self.c, pid, opted_in=RECORDED, source="dm")
        assert batch is not None
        kinds = [e.type for e in await self.memory.entities(GUILD_A, self.c)]
        self.assertEqual(kinds.count("deed"), 1)
        self.assertEqual(await self.waiting(), [])
        with self.assertRaises(MemoryRuleError):  # (recorded once)
            await self.deeds.record(GUILD_A, self.c, pid, opted_in=RECORDED, source="dm")
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertEqual(
            [e.type for e in await self.memory.entities(GUILD_A, self.c)].count("deed"), 0
        )

    async def test_two_presses_at_once_record_one_deed(self) -> None:
        import asyncio

        pid = await self.one()
        results = await asyncio.gather(
            self.deeds.record(GUILD_A, self.c, pid, opted_in=RECORDED, source="dm"),
            self.deeds.record(GUILD_A, self.c, pid, opted_in=RECORDED, source="dm"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, int) for r in results), 1)
        self.assertEqual(sum(isinstance(r, MemoryRuleError) for r in results), 1)
        kinds = [e.type for e in await self.memory.entities(GUILD_A, self.c)]
        self.assertEqual(kinds.count("deed"), 1)

    async def test_a_refusal_by_the_store_leaves_it_waiting(self) -> None:
        await self.put((deed(doer="Brindle"), (DM_ID,)), session=902)  # (a place cannot do a deed)
        (w,) = [x for x in await self.waiting() if x.doer == "Brindle"]
        with self.assertRaises(MemoryRuleError):
            await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")
        self.assertIn(w.id, [x.id for x in await self.waiting()])

    async def test_a_press_refused_for_a_name_creates_no_party(self) -> None:
        await self.put((deed(doer="the party", viewers=("Nowhere",)), (DM_ID,)), session=903)
        (w,) = [x for x in await self.waiting() if x.viewers == ("Nowhere",)]
        with self.assertRaises(MemoryRuleError):
            await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")
        self.assertIsNone(await self.memory.party(GUILD_A, self.c))

    async def test_nothing_but_the_dm_records_or_dismisses(self) -> None:
        pid = await self.one()
        for source in ("extractor", "cleaner", "entitybot"):
            with self.assertRaises(MemoryRuleError):
                await self.deeds.record(GUILD_A, self.c, pid, opted_in=RECORDED, source=source)
            with self.assertRaises(MemoryRuleError):
                await self.deeds.dismiss(GUILD_A, self.c, pid, source=source)
        self.assertEqual(len(await self.waiting()), 1)

    async def test_a_name_dmbot_does_not_know_is_refused_in_plain_words(self) -> None:
        await self.put((deed(viewers=("Nowhere",)), (DM_ID,)), session=900)
        (w,) = [x for x in await self.waiting() if x.viewers == ("Nowhere",)]
        with self.assertRaisesRegex(MemoryRuleError, "doesn't know who “Nowhere”"):
            await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")

    async def test_a_speaker_who_stopped_means_it_cannot_be_recorded(self) -> None:
        pid = await self.one()
        with self.assertRaises(MemoryRuleError):
            await self.deeds.record(GUILD_A, self.c, pid, opted_in={ALICE}, source="dm")

    async def test_the_party_is_a_doer(self) -> None:
        await self.put((deed(doer="the party"), (DM_ID,)), session=901)
        (w,) = [x for x in await self.waiting() if x.doer == "the party"]
        batch = await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")
        self.assertIsNotNone(batch)

    async def test_dismiss_turns_it_down_for_good(self) -> None:
        pid = await self.one()
        await self.deeds.dismiss(GUILD_A, self.c, pid, source="dm")
        self.assertEqual(await self.waiting(), [])
        with self.assertRaises(MemoryRuleError):
            await self.deeds.dismiss(GUILD_A, self.c, pid, source="dm")


def rescue() -> DeedProposal:
    return DeedProposal("Cerric", "Saved Brindle", ("Brindle",), "Helped them", ("1",), "rescue")


class TheKind(Base):
    async def kinds(self) -> list[str]:
        out = []
        for r in await self.memory.relations(GUILD_A, self.c, include_secret=True):
            if r.predicate == "did":
                out.append(r.detail)
        return out

    async def test_record_keeps_the_kind_in_did_and_undo_takes_it_away(self) -> None:
        await self.put(
            (
                DeedProposal(
                    "Cerric", "Saved Brindle", ("Brindle",), "Helped them", ("1",), "rescue"
                ),
                (DM_ID,),
            )
        )
        (w,) = await self.waiting()
        self.assertEqual(w.kind, "rescue")
        batch = await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")
        assert batch is not None
        self.assertEqual(await self.kinds(), ["rescue"])
        await self.memory.undo(GUILD_A, self.c, batch, source="dm")
        self.assertEqual(await self.kinds(), [])

    async def test_no_kind_is_allowed_and_reads_as_empty(self) -> None:
        await self.put((deed(), (DM_ID,)))
        (w,) = await self.waiting()
        self.assertEqual(w.kind, "")
        await self.deeds.record(GUILD_A, self.c, w.id, opted_in=RECORDED, source="dm")
        self.assertEqual(await self.kinds(), [""])

    async def test_add_deed_refuses_a_kind_outside_the_list(self) -> None:
        with self.assertRaisesRegex(MemoryRuleError, "kind of deed"):
            await self.memory.add_deed(
                GUILD_A, self.c, self.cerric, "Saved it", [self.brindle], "Helped them",
                source="dm", kind="murder",
            )  # fmt: skip

    async def test_a_backup_keeps_it_and_an_older_row_without_it_loads_as_empty(self) -> None:
        await self.put(
            (
                DeedProposal(
                    "Cerric", "Saved Brindle", ("Brindle",), "Helped them", ("1",), "rescue"
                ),
                (DM_ID,),
            )
        )
        section = DeedProposalsSection()
        async with self.db.guild(GUILD_A) as conn:
            (row,) = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
        self.assertEqual(row["kind"], "rescue")
        old = {k: v for k, v in row.items() if k != "kind"}  # (a file from before #3236)
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, section.check([old]))
        (w,) = await self.waiting()
        self.assertEqual(w.kind, "")
        with self.assertRaises(CampaignError):
            section.check([{**row, "kind": "murder"}])


class Backup(Base):
    async def test_a_backup_round_trips_the_proposals(self) -> None:
        await self.put((deed(), (DM_ID,)))
        section = DeedProposalsSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
        self.assertEqual(await self.waiting(), [])
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, section.check(rows))
        (w,) = await self.waiting()
        self.assertEqual((w.doer, w.text), ("Cerric", "Saved the mill"))

    async def test_a_damaged_section_is_refused_whole(self) -> None:
        await self.put((deed(), (DM_ID,)))
        async with self.db.guild(GUILD_A) as conn:
            (good,) = await DeedProposalsSection().dump(conn, GUILD_A, self.c)
        DeedProposalsSection().check([good])
        for bad in (
            {**good, "effect": "Loved them"}, {**good, "status": "done"}, {**good, "viewers": []},
            {**good, "speaker_ids": [True]}, {**good, "id": "x"}, {**good, "extra": 1},
            {**good, "text": ""},
        ):  # fmt: skip
            with self.assertRaises(CampaignError):
                DeedProposalsSection().check([bad])
        with self.assertRaises(CampaignError):
            DeedProposalsSection().check([good, good])  # one id once
