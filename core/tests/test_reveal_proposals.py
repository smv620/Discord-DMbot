"""Who learned what, waiting for the DM (#2996, part 2 slice 1, PR 3): the words placed on a stored
statement or dropped, ids only in the row, nothing to learn and a teller who doesn't know it both
dropped, one per (character, statement) per session, consent, Stop recording me, the backup and
isolation. Invented names (Tran, Bruno, Clyde, Shela); needs Postgres."""

from __future__ import annotations

from typing import Any

from dmbot.campaigns.models import CampaignError
from dmbot.memory.claims import ClaimStore
from dmbot.memory.gate import CONNECTION
from dmbot.memory.models import CONFIRMED
from dmbot.memory.reveal_proposals import RevealProposals, Stored, forget
from dmbot.memory.reveal_proposals_backup import RevealProposalsSection
from dmbot.memory.reveal_reading import RevealProposal
from tests.test_gate import Helpers
from tests.test_memory_store import GUILD_A, GUILD_B

DM_ID, ALICE, BOB = 7, 8, 9
RECORDED = {DM_ID, ALICE}


def check(
    who: str = "Tran", subject: str = "Bruno", rel: str = "knows", obj: str = "Shela"
) -> RevealProposal:
    return RevealProposal("check", who, subject, rel, obj, ("1", "2"), check="Insight")


def tell(
    who: str = "Tran", teller: str = "Clyde", subject: str = "Bruno", obj: str = "Shela"
) -> RevealProposal:
    return RevealProposal("tell", who, subject, "knows", obj, ("3",), teller=teller)


class Base(Helpers):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.ids = await self.table()
        self.tran = await self.add("Tran", type="character", role="npc", status=CONFIRMED)
        self.clyde = await self.add("Clyde", type="character", role="npc", status=CONFIRMED)
        self.rel = await self.link(self.ids["bruno"], self.ids["shela"])  # (a secret)
        self.reveals = RevealProposals(self.memory)

    async def put(
        self,
        *proposals: tuple[RevealProposal, tuple[int, ...]],
        session: int | None = 500,
        recorded: set[int] = RECORDED,
        campaign: str | None = None,
    ) -> Stored:
        return await self.reveals.store(
            GUILD_A, campaign or self.c, list(proposals), opted_in=recorded,
            session_started_at=session, prompt_version="reveals-1", model="m",
        )  # fmt: skip

    async def rows(self) -> list[Any]:
        async with self.db.guild(GUILD_A) as conn:
            cur = await conn.execute("SELECT * FROM memory_reveal_proposals ORDER BY created_at")
            return list(await cur.fetchall())


class Placing(Base):
    async def test_a_check_is_placed_on_the_stored_connection_ids_only(self) -> None:
        got = await self.put((check(), (DM_ID,)))
        self.assertEqual(got.stored, 1)
        (row,) = await self.rows()
        self.assertEqual(
            (row["character_id"], row["statement_kind"], row["statement_id"], row["how"],
             row["check_name"], row["teller_id"], row["lines"], row["speaker_ids"]),
            (self.tran, CONNECTION, self.rel, "check", "Insight", None, ["1", "2"], [DM_ID]),
        )  # fmt: skip
        words = {"Tran", "Bruno", "Shela", "knows"}
        self.assertFalse(words & {str(v) for v in row.values()})  # (no words in the row)

    async def test_the_relationship_said_from_the_other_end_places_too(self) -> None:
        self.assertEqual(
            (await self.put((check(subject="Shela", obj="Bruno"), (DM_ID,)))).stored, 1
        )

    async def test_an_entry_alone_is_learning_it_exists(self) -> None:
        got = await self.put((check(subject="The Sword", rel="", obj=""), (DM_ID,)))
        self.assertEqual(got.stored, 1)
        (row,) = await self.rows()
        self.assertEqual((row["statement_kind"], row["statement_id"]), ("entry", self.ids["sword"]))

    async def test_words_that_place_on_no_single_statement_are_dropped_and_counted(self) -> None:
        cases = (
            check(subject="Nobody"),  # an unknown name
            check(rel="serves"),  # no such connection between them
            check(subject="Bruno", obj="The Sword"),  # no connection at all
        )
        got = await self.put(*((c, (DM_ID,)) for c in cases))
        self.assertEqual((got.stored, got.counts and got.counts["reveal_unplaced"]), (0, 3))

    async def test_the_one_who_learned_and_a_teller_are_characters(self) -> None:
        group = await self.add("The Irendi", type="faction", status=CONFIRMED)
        assert group
        got = await self.put(
            (check(who="The Irendi"), (DM_ID,)), (check(who="The Sword"), (DM_ID,)),
            (tell(teller="The Irendi"), (DM_ID,)),
        )  # fmt: skip
        self.assertEqual((got.stored, got.counts and got.counts["reveal_not_a_character"]), (0, 3))


class WhatCanBeLearned(Base):
    async def test_something_the_character_can_already_see_is_not_proposed(self) -> None:
        await self.know(CONNECTION, self.rel, [self.tran])
        got = await self.put((check(), (DM_ID,)))
        self.assertEqual((got.stored, got.counts and got.counts["reveal_known"]), (0, 1))

    async def test_a_teller_who_does_not_know_it_tells_nothing(self) -> None:
        got = await self.put((tell(), (DM_ID,)))
        self.assertEqual((got.stored, got.counts and got.counts["reveal_teller_ignorant"]), (0, 1))

    async def test_a_teller_who_knows_it_tells_the_listener_only(self) -> None:
        await self.know(CONNECTION, self.rel, [self.clyde])
        self.assertEqual((await self.put((tell(), (DM_ID,)))).stored, 1)
        (row,) = await self.rows()
        self.assertEqual((row["character_id"], row["teller_id"]), (self.tran, self.clyde))
        # (the proposal is only a proposal: nobody learned anything yet)
        got = await self.memory.statement_knowers(GUILD_A, self.c, CONNECTION, [self.rel])
        self.assertEqual(got[self.rel], [self.clyde])


class OncePerSession(Base):
    async def test_one_per_character_and_statement_across_re_runs_and_wordings(self) -> None:
        first = await self.put((check(), (DM_ID,)))
        again = await self.put((check(subject="Shela", obj="Bruno"), (DM_ID,)))  # (same one)
        self.assertEqual((first.stored, again.stored), (1, 0))
        self.assertEqual(again.counts and again.counts["reveal_repeated"], 1)
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute("UPDATE memory_reveal_proposals SET status = 'dismissed'")
        self.assertEqual((await self.put((check(), (DM_ID,)))).stored, 0)  # (any status)
        self.assertEqual((await self.put((check(), (DM_ID,)), session=900)).stored, 1)


class Consent(Base):
    async def test_a_speaker_not_recorded_now_keeps_it_from_being_stored(self) -> None:
        got = await self.put((check(), (DM_ID, BOB)))
        self.assertEqual((got.stored, got.counts and got.counts["reveal_not_recorded"]), (0, 1))

    async def test_a_speaker_who_stops_wipes_the_waiting_one_and_the_count_leaves_it_out(
        self,
    ) -> None:
        await self.put((check(), (DM_ID,)))
        self.assertEqual(await self.reveals.count_waiting(GUILD_A, self.c, opted_in=RECORDED), 1)
        self.assertEqual(await self.reveals.count_waiting(GUILD_A, self.c, opted_in={ALICE}), 0)
        self.assertEqual(await self.reveals.purge_stopped(GUILD_A, self.c, {ALICE}), 1)
        self.assertEqual(await self.rows(), [])

    async def test_stop_recording_me_deletes_waiting_ones_and_scrubs_a_kept_one(self) -> None:
        await self.put((check(), (ALICE,)), (check(subject="The Sword", rel="", obj=""), (ALICE,)))
        async with self.db.guild(GUILD_A) as conn:
            await conn.execute(
                "UPDATE memory_reveal_proposals SET status = 'kept' WHERE statement_kind = 'entry'"
            )
            gone = await forget(conn, GUILD_A, ALICE)
        (left,) = await self.rows()
        self.assertEqual(gone, 1)
        self.assertEqual((left["status"], left["speaker_ids"], left["lines"]), ("kept", [], []))

    async def test_the_claims_stop_recording_me_path_clears_them_too(self) -> None:
        await self.put((check(), (ALICE,)))
        await ClaimStore(self.memory).forget_speaker(GUILD_A, ALICE)
        self.assertEqual(await self.rows(), [])


class BackupAndIsolation(Base):
    async def test_a_backup_round_trips_the_proposals(self) -> None:
        await self.know(CONNECTION, self.rel, [self.clyde])
        await self.put((check(subject="The Sword", rel="", obj=""), (DM_ID,)), (tell(), (DM_ID,)))
        section = RevealProposalsSection()
        async with self.db.guild(GUILD_A) as conn:
            rows = await section.dump(conn, GUILD_A, self.c)
            await section.clear(conn, GUILD_A, self.c)
        self.assertEqual(await self.rows(), [])
        async with self.db.guild(GUILD_A) as conn:
            await section.load(conn, GUILD_A, self.c, section.check(rows))
        self.assertEqual(sorted(r["how"] for r in await self.rows()), ["check", "tell"])

    async def test_a_damaged_section_is_refused_whole(self) -> None:
        await self.put((check(), (DM_ID,)))
        async with self.db.guild(GUILD_A) as conn:
            (good,) = await RevealProposalsSection().dump(conn, GUILD_A, self.c)
        RevealProposalsSection().check([good])
        for bad in (
            {**good, "status": "done"}, {**good, "statement_kind": "story"},
            {**good, "check_name": "Athletics"}, {**good, "teller_id": "a" * 32},
            {**good, "how": "tell"}, {**good, "speaker_ids": [True]}, {**good, "id": "x"},
            {**good, "extra": 1}, {**good, "lines": ["1"] * 11},
        ):  # fmt: skip
            with self.subTest(bad), self.assertRaises(CampaignError):
                RevealProposalsSection().check([bad])
        with self.assertRaises(CampaignError):
            RevealProposalsSection().check([good, good])  # (one id once)

    async def test_another_campaign_places_nothing_on_this_one_s_statements(self) -> None:
        other = (await self.campaigns.create(GUILD_B, "Elsewhere", 9)).id
        got = await self.reveals.store(
            GUILD_B, other, [(check(), (DM_ID,))], opted_in=RECORDED, session_started_at=500,
            prompt_version="reveals-1", model="m",
        )  # fmt: skip
        self.assertEqual((got.stored, got.counts and got.counts["reveal_not_a_character"]), (0, 1))
        self.assertEqual(await self.rows(), [])
